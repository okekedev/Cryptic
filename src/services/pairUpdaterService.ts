// src/services/pairUpdaterService.ts

import https from "https";
import fs from "fs/promises";
import path from "path";
import { ACTIVE_USD_PAIRS, LIMIT_ONLY_USD_PAIRS } from "../constants/UsdPairs";

interface CoinbaseProduct {
  id: string;
  base_currency: string;
  quote_currency: string;
  status: string;
  trading_disabled: boolean;
  limit_only: boolean;
  [key: string]: any;
}

interface LastUpdateInfo {
  date: string;
  weekNumber: number;
  year: number;
}

export class PairUpdaterService {
  private updateCheckInterval: NodeJS.Timeout | null = null;
  private lastUpdateFile = path.join(__dirname, "../../.last-pair-update.json");
  private usdPairsFile = path.join(__dirname, "../constants/UsdPairs.ts");
  private isUpdating = false;

  // Check every 24 hours
  private readonly CHECK_INTERVAL = 24 * 60 * 60 * 1000; // 24 hours in milliseconds

  constructor() {
    console.log("📊 Pair Updater Service initialized");
  }

  /**
   * Start the automated update service
   */
  start(): void {
    console.log("🚀 Starting pair updater service...");

    // Run initial check
    this.checkAndUpdate();

    // Set up interval to check every 24 hours
    this.updateCheckInterval = setInterval(() => {
      this.checkAndUpdate();
    }, this.CHECK_INTERVAL);
  }

  /**
   * Stop the service
   */
  stop(): void {
    if (this.updateCheckInterval) {
      clearInterval(this.updateCheckInterval);
      this.updateCheckInterval = null;
      console.log("🛑 Pair updater service stopped");
    }
  }

  /**
   * Check if it's a new week and update if necessary
   */
  private async checkAndUpdate(): Promise<void> {
    if (this.isUpdating) {
      console.log("⏳ Update already in progress, skipping...");
      return;
    }

    try {
      this.isUpdating = true;
      const now = new Date();
      const currentWeek = this.getWeekNumber(now);
      const currentYear = now.getFullYear();

      console.log(`🔍 Checking for updates - ${now.toLocaleString()}`);
      console.log(`   Current week: ${currentWeek}, Year: ${currentYear}`);

      // Check last update info
      const lastUpdate = await this.getLastUpdateInfo();

      if (!lastUpdate) {
        console.log(
          "📝 No previous update record found, running initial update..."
        );
        await this.performUpdate(now, currentWeek, currentYear);
        return;
      }

      // Check if it's a new week
      const isNewWeek =
        currentYear > lastUpdate.year ||
        (currentYear === lastUpdate.year &&
          currentWeek > lastUpdate.weekNumber);

      if (isNewWeek) {
        console.log(
          `📅 New week detected! Last update was week ${lastUpdate.weekNumber}, ${lastUpdate.year}`
        );
        await this.performUpdate(now, currentWeek, currentYear);
      } else {
        console.log(
          `✓ Already updated this week (week ${currentWeek}, ${currentYear})`
        );
      }
    } catch (error) {
      console.error("❌ Error in checkAndUpdate:", error);
    } finally {
      this.isUpdating = false;
    }
  }

  /**
   * Perform the actual update
   */
  private async performUpdate(
    now: Date,
    weekNumber: number,
    year: number
  ): Promise<void> {
    console.log("🔄 Performing weekly pair update...");

    try {
      // Fetch current products from Coinbase
      const products = await this.fetchAllProducts();
      const { active, limitOnly } = this.filterUSDPairs(products);

      // Compare with current pairs
      const { hasChanges, report } = this.comparePairs(active, limitOnly);

      if (hasChanges) {
        console.log("📝 Changes detected, updating UsdPairs.ts...");
        await this.updateUsdPairsFile(active, limitOnly);
        this.printReport(report);
      } else {
        console.log("✓ No changes detected - pairs are up to date");
      }

      // Save last update info
      await this.saveLastUpdateInfo({
        date: now.toISOString(),
        weekNumber,
        year,
      });
    } catch (error) {
      console.error("❌ Error performing update:", error);
    }
  }

  /**
   * Fetch all products from Coinbase API
   */
  private fetchAllProducts(): Promise<CoinbaseProduct[]> {
    return new Promise((resolve, reject) => {
      const options = {
        hostname: "api.exchange.coinbase.com",
        path: "/products",
        method: "GET",
        headers: {
          "Content-Type": "application/json",
          "User-Agent": "CryptoTradingBot/1.0",
        },
      };

      const req = https.request(options, (res) => {
        let data = "";

        res.on("data", (chunk) => {
          data += chunk;
        });

        res.on("end", () => {
          try {
            const products = JSON.parse(data);
            console.log(`✓ Fetched ${products.length} products from Coinbase`);
            resolve(Array.isArray(products) ? products : []);
          } catch (error) {
            reject(new Error(`Failed to parse response: ${error}`));
          }
        });
      });

      req.on("error", reject);
      req.setTimeout(30000, () => {
        req.abort();
        reject(new Error("Request timeout"));
      });

      req.end();
    });
  }

  /**
   * Filter USD pairs from products
   */
  private filterUSDPairs(products: CoinbaseProduct[]): {
    active: string[];
    limitOnly: string[];
  } {
    const active: string[] = [];
    const limitOnly: string[] = [];

    products.forEach((product) => {
      if (
        product.quote_currency === "USD" &&
        product.status === "online" &&
        !product.trading_disabled
      ) {
        if (product.limit_only) {
          limitOnly.push(product.id);
        } else {
          active.push(product.id);
        }
      }
    });

    active.sort();
    limitOnly.sort();

    return { active, limitOnly };
  }

  /**
   * Compare current pairs with fetched pairs
   */
  private comparePairs(fetchedActive: string[], fetchedLimitOnly: string[]) {
    const currentActiveSet = new Set(ACTIVE_USD_PAIRS);
    const fetchedActiveSet = new Set(fetchedActive);

    const currentLimitSet = new Set(LIMIT_ONLY_USD_PAIRS);
    const fetchedLimitSet = new Set(fetchedLimitOnly);

    const newActivePairs = fetchedActive.filter(
      (p) => !currentActiveSet.has(p as typeof ACTIVE_USD_PAIRS[number])
    );
    const removedActivePairs = [...ACTIVE_USD_PAIRS].filter(
      (p) => !fetchedActiveSet.has(p as string)
    );

    const newLimitPairs = fetchedLimitOnly.filter(
      (p) => !currentLimitSet.has(p as typeof LIMIT_ONLY_USD_PAIRS[number])
    );
    const removedLimitPairs = [...LIMIT_ONLY_USD_PAIRS].filter(
      (p) => !fetchedLimitSet.has(p as string)
    );

    const hasChanges =
      newActivePairs.length > 0 ||
      removedActivePairs.length > 0 ||
      newLimitPairs.length > 0 ||
      removedLimitPairs.length > 0;

    return {
      hasChanges,
      report: {
        newActivePairs,
        removedActivePairs,
        newLimitPairs,
        removedLimitPairs,
        totalActive: fetchedActive.length,
        totalLimitOnly: fetchedLimitOnly.length,
      },
    };
  }

  /**
   * Update the UsdPairs.ts file
   */
  private async updateUsdPairsFile(
    activePairs: string[],
    limitOnlyPairs: string[]
  ): Promise<void> {
    const date = new Date().toLocaleDateString("en-US", {
      month: "long",
      year: "numeric",
    });

    const content = `// src/constants/UsdPairs.ts
// Last updated: ${date}
// Active USD trading pairs from Coinbase (status: online, trading_disabled: false)
// Auto-updated by PairUpdaterService

export const ACTIVE_USD_PAIRS = [
${activePairs.map((pair) => `  '${pair}',`).join("\n")}
] as const;

export type USDPair = typeof ACTIVE_USD_PAIRS[number];

// Additional pairs that are limit-only (less liquid, but still tradeable)
export const LIMIT_ONLY_USD_PAIRS = [
${limitOnlyPairs.map((pair) => `  '${pair}',`).join("\n")}
] as const;

// All pairs including limit-only
export const ALL_USD_PAIRS = [...ACTIVE_USD_PAIRS, ...LIMIT_ONLY_USD_PAIRS] as const;

// Popular pairs for quick access
export const POPULAR_USD_PAIRS = [
  'BTC-USD',
  'ETH-USD',
  'SOL-USD',
  'XRP-USD',
  'ADA-USD',
  'AVAX-USD',
  'DOGE-USD',
  'DOT-USD',
  'MATIC-USD',
  'LINK-USD',
  'UNI-USD',
  'ATOM-USD',
  'LTC-USD',
  'BCH-USD',
  'NEAR-USD',
  'ARB-USD',
  'OP-USD',
  'APT-USD',
  'ICP-USD',
  'SHIB-USD',
] as const;

// Helper functions
export function isValidUSDPair(pair: string): pair is USDPair {
  return ACTIVE_USD_PAIRS.includes(pair as USDPair);
}

export function getUSDPairInfo() {
  return {
    active: ACTIVE_USD_PAIRS.length,
    limitOnly: LIMIT_ONLY_USD_PAIRS.length,
    total: ALL_USD_PAIRS.length,
    lastUpdated: '${date}',
  };
}`;

    // Backup current file
    try {
      const currentContent = await fs.readFile(this.usdPairsFile, "utf-8");
      const backupPath = this.usdPairsFile.replace(
        ".ts",
        `.backup-${Date.now()}.ts`
      );
      await fs.writeFile(backupPath, currentContent);
      console.log(`📋 Backed up current file`);
    } catch {
      console.log("📝 No existing file to backup");
    }

    // Write new file
    await fs.writeFile(this.usdPairsFile, content);
    console.log(`✅ Updated UsdPairs.ts`);
  }

  /**
   * Get the week number of the year
   */
  private getWeekNumber(date: Date): number {
    const firstDayOfYear = new Date(date.getFullYear(), 0, 1);
    const pastDaysOfYear =
      (date.getTime() - firstDayOfYear.getTime()) / 86400000;
    return Math.ceil((pastDaysOfYear + firstDayOfYear.getDay() + 1) / 7);
  }

  /**
   * Get last update info from file
   */
  private async getLastUpdateInfo(): Promise<LastUpdateInfo | null> {
    try {
      const data = await fs.readFile(this.lastUpdateFile, "utf-8");
      return JSON.parse(data);
    } catch {
      return null;
    }
  }

  /**
   * Save last update info to file
   */
  private async saveLastUpdateInfo(info: LastUpdateInfo): Promise<void> {
    await fs.writeFile(this.lastUpdateFile, JSON.stringify(info, null, 2));
  }

  /**
   * Print update report
   */
  private printReport(report: any): void {
    console.log("\n📊 Update Report:");
    console.log("================");

    if (report.newActivePairs.length > 0) {
      console.log(`\n✨ New Active Pairs (${report.newActivePairs.length}):`);
      report.newActivePairs.forEach((pair: string) =>
        console.log(`  + ${pair}`)
      );
    }

    if (report.removedActivePairs.length > 0) {
      console.log(
        `\n❌ Removed Active Pairs (${report.removedActivePairs.length}):`
      );
      report.removedActivePairs.forEach((pair: string) =>
        console.log(`  - ${pair}`)
      );
    }

    if (report.newLimitPairs.length > 0) {
      console.log(
        `\n✨ New Limit-Only Pairs (${report.newLimitPairs.length}):`
      );
      report.newLimitPairs.forEach((pair: string) =>
        console.log(`  + ${pair}`)
      );
    }

    if (report.removedLimitPairs.length > 0) {
      console.log(
        `\n❌ Removed Limit-Only Pairs (${report.removedLimitPairs.length}):`
      );
      report.removedLimitPairs.forEach((pair: string) =>
        console.log(`  - ${pair}`)
      );
    }

    console.log(`\n📈 Total Active: ${report.totalActive}`);
    console.log(`📉 Total Limit-Only: ${report.totalLimitOnly}`);
  }
}

// Export singleton instance
export const pairUpdaterService = new PairUpdaterService();
