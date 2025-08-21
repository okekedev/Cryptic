// src/utils/coinbaseApiClient.ts

import https from "https";

export interface CoinbaseProduct {
  id: string;
  base_currency: string;
  quote_currency: string;
  quote_increment: string;
  base_increment: string;
  display_name: string;
  min_market_funds: string;
  margin_enabled: boolean;
  post_only: boolean;
  limit_only: boolean;
  cancel_only: boolean;
  status: string;
  status_message: string;
  trading_disabled: boolean;
  fx_stablecoin: boolean;
  max_slippage_percentage: string;
  auction_mode: boolean;
  high_bid_limit_percentage: string;
}

export class CoinbaseApiClient {
  private readonly baseUrl = "api.coinbase.com";

  /**
   * Fetch all available trading products from Coinbase
   */
  public async getAllProducts(): Promise<CoinbaseProduct[]> {
    return new Promise((resolve, reject) => {
      const options = {
        hostname: this.baseUrl,
        path: "/api/v3/brokerage/products",
        method: "GET",
        headers: {
          "Content-Type": "application/json",
        },
      };

      const req = https.request(options, (res) => {
        let data = "";

        res.on("data", (chunk) => {
          data += chunk;
        });

        res.on("end", () => {
          try {
            const response = JSON.parse(data);
            resolve(response.products || []);
          } catch (error) {
            reject(new Error(`Failed to parse response: ${error}`));
          }
        });
      });

      req.on("error", (error) => {
        reject(error);
      });

      req.end();
    });
  }

  /**
   * Get all USD trading pairs that are currently online
   */
  public async getActiveUSDPairs(): Promise<string[]> {
    try {
      const products = await this.getAllProducts();

      // Filter for USD pairs that are online and not disabled
      const usdPairs = products
        .filter(
          (product) =>
            product.quote_currency === "USD" &&
            product.status === "online" &&
            !product.trading_disabled &&
            !product.limit_only // Skip limit-only products as they might have less activity
        )
        .map((product) => product.id)
        .sort();

      console.log(`Found ${usdPairs.length} active USD trading pairs`);
      return usdPairs;
    } catch (error) {
      console.error("Error fetching USD pairs:", error);
      return [];
    }
  }

  /**
   * Get popular USD trading pairs (top cryptocurrencies)
   */
  public getPopularUSDPairs(): string[] {
    return [
      "BTC-USD", // Bitcoin
      "ETH-USD", // Ethereum
      "SOL-USD", // Solana
      "BNB-USD", // Binance Coin
      "XRP-USD", // Ripple
      "ADA-USD", // Cardano
      "AVAX-USD", // Avalanche
      "DOGE-USD", // Dogecoin
      "TRX-USD", // TRON
      "LINK-USD", // Chainlink
      "DOT-USD", // Polkadot
      "MATIC-USD", // Polygon
      "ICP-USD", // Internet Computer
      "SHIB-USD", // Shiba Inu
      "LTC-USD", // Litecoin
      "BCH-USD", // Bitcoin Cash
      "UNI-USD", // Uniswap
      "ATOM-USD", // Cosmos
      "XLM-USD", // Stellar
      "ETC-USD", // Ethereum Classic
      "HBAR-USD", // Hedera
      "FIL-USD", // Filecoin
      "NEAR-USD", // NEAR Protocol
      "APT-USD", // Aptos
      "ARB-USD", // Arbitrum
      "OP-USD", // Optimism
      "ALGO-USD", // Algorand
      "AAVE-USD", // Aave
      "MKR-USD", // Maker
      "SAND-USD", // The Sandbox
    ];
  }
}
