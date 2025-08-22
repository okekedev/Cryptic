import fs from "fs/promises";
import path from "path";

interface CacheData {
  pairs: string[];
  timestamp: number;
  source: "api" | "fallback";
}

export class CacheManager {
  private cacheDir: string;
  private cacheFile: string;
  private cacheExpiry: number;

  constructor(
    cacheDir: string = "./cache",
    cacheExpiry: number = 24 * 60 * 60 * 1000
  ) {
    // 24 hours default
    this.cacheDir = cacheDir;
    this.cacheFile = path.join(cacheDir, "usd-pairs.json");
    this.cacheExpiry = cacheExpiry;
  }

  private async ensureCacheDir(): Promise<void> {
    try {
      await fs.mkdir(this.cacheDir, { recursive: true });
    } catch (error) {
      console.error("Failed to create cache directory:", error);
    }
  }

  async loadCache(): Promise<string[] | null> {
    try {
      await this.ensureCacheDir();

      const data = await fs.readFile(this.cacheFile, "utf-8");
      const cache: CacheData = JSON.parse(data);

      // Check if cache is expired
      const age = Date.now() - cache.timestamp;
      const ageInHours = Math.floor(age / (1000 * 60 * 60));

      console.log(
        `   - Cache found: ${cache.pairs.length} pairs, age: ${ageInHours} hours, source: ${cache.source}`
      );

      if (age > this.cacheExpiry) {
        console.log(
          `   - Cache expired (older than ${
            this.cacheExpiry / (1000 * 60 * 60)
          } hours)`
        );
        return null;
      }

      return cache.pairs;
    } catch (error) {
      if ((error as any).code === "ENOENT") {
        console.log("   - No cache file found");
      } else {
        console.error("   - Error loading cache:", error);
      }
      return null;
    }
  }

  async saveCache(
    pairs: string[],
    source: "api" | "fallback" = "api"
  ): Promise<void> {
    try {
      await this.ensureCacheDir();

      const cache: CacheData = {
        pairs,
        timestamp: Date.now(),
        source,
      };

      await fs.writeFile(this.cacheFile, JSON.stringify(cache, null, 2));
      console.log(`   - Cache saved: ${pairs.length} pairs from ${source}`);
    } catch (error) {
      console.error("   - Error saving cache:", error);
    }
  }

  async clearCache(): Promise<void> {
    try {
      await fs.unlink(this.cacheFile);
      console.log("   - Cache cleared");
    } catch (error) {
      if ((error as any).code !== "ENOENT") {
        console.error("   - Error clearing cache:", error);
      }
    }
  }

  async getCacheInfo(): Promise<{
    exists: boolean;
    age?: number;
    pairs?: number;
    source?: string;
  } | null> {
    try {
      const data = await fs.readFile(this.cacheFile, "utf-8");
      const cache: CacheData = JSON.parse(data);
      const age = Date.now() - cache.timestamp;

      return {
        exists: true,
        age: Math.floor(age / 1000), // age in seconds
        pairs: cache.pairs.length,
        source: cache.source,
      };
    } catch (error) {
      return { exists: false };
    }
  }
}
