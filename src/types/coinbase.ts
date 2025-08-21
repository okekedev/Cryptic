// src/types/coinbase.ts

export interface CoinbaseWebSocketMessage {
  type: string;
  sequence?: number;
  time?: string;
  product_id?: string;
  [key: string]: any;
}

export interface TickerMessage extends CoinbaseWebSocketMessage {
  type: "ticker";
  product_id: string;
  price: string;
  open_24h: string;
  volume_24h: string;
  low_24h: string;
  high_24h: string;
  volume_30d: string;
  best_bid: string;
  best_bid_size: string;
  best_ask: string;
  best_ask_size: string;
  side: "buy" | "sell";
  time: string;
  trade_id: number;
  last_size: string;
}

export interface Level2Update extends CoinbaseWebSocketMessage {
  type: "l2update";
  product_id: string;
  changes: Array<["buy" | "sell", string, string]>; // [side, price, size]
  time: string;
}

export interface HeartbeatMessage extends CoinbaseWebSocketMessage {
  type: "heartbeat";
  sequence: number;
  last_trade_id: number;
  product_id: string;
  time: string;
}

// Simplified without JWT
export interface SubscriptionMessage {
  type: "subscribe" | "unsubscribe";
  product_ids: string[];
  channel: string;
}

export interface ErrorMessage extends CoinbaseWebSocketMessage {
  type: "error";
  message: string;
  reason?: string;
}

export interface SubscriptionsMessage extends CoinbaseWebSocketMessage {
  type: "subscriptions";
  channels: Array<{
    name: string;
    product_ids: string[];
  }>;
}
