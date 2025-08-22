// src/utils/coinbaseApiClient.ts

import https from "https";

export interface CoinbaseProduct {
  id: string;
  base_currency: string;
  quote_currency: string;
  base_min_size: string;
  base_max_size: string;
  quote_increment: string;
  base_increment: string;
  display_name: string;
  min_market_funds: string;
  max_market_funds: string;
  margin_enabled: boolean;
  fx_stablecoin: boolean;
  max_slippage_percentage: string;
  post_only: boolean;
  limit_only: boolean;
  cancel_only: boolean;
  trading_disabled: boolean;
  status: string;
  status_message: string;
  auction_mode: boolean;
}

export class CoinbaseApiClient {
  private readonly baseUrl = "api.exchange.coinbase.com";

  /**
   * Fetch all available trading products from Coinbase
   */
  public async getAllProducts(): Promise<CoinbaseProduct[]> {
    return new Promise((resolve, reject) => {
      console.log(`   - Making HTTPS request to ${this.baseUrl}/products`);

      const options = {
        hostname: this.baseUrl,
        path: "/products",
        method: "GET",
        headers: {
          "Content-Type": "application/json",
          "User-Agent": "CryptoTradingBot/1.0",
        },
      };

      const req = https.request(options, (res) => {
        console.log(`   - Response status: ${res.statusCode}`);
        let data = "";

        res.on("data", (chunk) => {
          data += chunk;
          console.log(`   - Received chunk: ${chunk.length} bytes`);
        });

        res.on("end", () => {
          console.log(`   - Total response size: ${data.length} bytes`);
          try {
            const response = JSON.parse(data);
            // The exchange API returns an array directly, not wrapped in an object
            const products = Array.isArray(response) ? response : [];
            console.log(
              `   - Parsed ${products.length} products from response`
            );
            resolve(products);
          } catch (error) {
            console.error(`   - Parse error:`, error);
            console.log(`   - Response preview: ${data.substring(0, 200)}...`);
            reject(new Error(`Failed to parse response: ${error}`));
          }
        });
      });

      req.on("error", (error) => {
        console.error(`   - Request error:`, error);
        reject(error);
      });

      req.setTimeout(10000, () => {
        console.error("   - Request timeout after 10 seconds");
        req.abort();
        reject(new Error("Request timeout"));
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
            !product.trading_disabled
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
