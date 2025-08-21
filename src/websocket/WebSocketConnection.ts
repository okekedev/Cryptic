// src/websocket/CoinbaseWebSocket.ts

import { EventEmitter } from "events";
import { WebSocketConnection, ConnectionState } from "./WebSocketConnection";
import {
  CoinbaseWebSocketMessage,
  SubscriptionMessage,
  TickerMessage,
  Level2Update,
  HeartbeatMessage,
  ErrorMessage,
} from "../types/coinbase";

export interface CoinbaseConfig {
  channels: string[];
  productIds: string[];
  auth?: {
    apiKey: string; // organizations/{org_id}/apiKeys/{key_id}
    apiSecret: string; // EC private key with newlines
  };
}

export class CoinbaseWebSocket extends EventEmitter {
  private connection: WebSocketConnection;
  private sequenceNumbers: Map<string, number> = new Map();
  private config: CoinbaseConfig;
  private messageBuffer: CoinbaseWebSocketMessage[] = [];
  private isProcessing = false;

  constructor(config: CoinbaseConfig) {
    super();
    this.config = config;

    const wsUrl = "wss://advanced-trade-ws.coinbase.com";

    this.connection = new WebSocketConnection({
      url: wsUrl,
      reconnect: true,
      reconnectInterval: 1000,
      maxReconnectAttempts: 10,
      pingInterval: 30000,
    });

    this.setupEventHandlers();
  }

  public connect(): void {
    this.connection.connect();
  }

  public disconnect(): void {
    this.connection.disconnect();
  }

  public subscribe(channels: string[], productIds: string[]): void {
    const subscribeMessage: SubscriptionMessage = {
      type: "subscribe",
      channels: channels,
      product_ids: productIds,
    };

    if (this.config.auth) {
      // Add authentication if provided
      // Implementation depends on Coinbase auth requirements
    }

    this.connection.send(subscribeMessage);
  }

  public unsubscribe(channels: string[], productIds: string[]): void {
    const unsubscribeMessage: SubscriptionMessage = {
      type: "unsubscribe",
      channels: channels,
      product_ids: productIds,
    };

    this.connection.send(unsubscribeMessage);
  }

  private setupEventHandlers(): void {
    this.connection.on("connected", () => {
      this.emit("connected");
      // Auto-subscribe to configured channels
      if (this.config.channels.length && this.config.productIds.length) {
        this.subscribe(this.config.channels, this.config.productIds);
      }
    });

    this.connection.on("disconnected", (data) => {
      this.emit("disconnected", data);
    });

    this.connection.on("error", (error) => {
      this.emit("error", error);
    });

    this.connection.on("message", (message: CoinbaseWebSocketMessage) => {
      this.handleMessage(message);
    });

    this.connection.on("reconnecting", (data) => {
      this.emit("reconnecting", data);
    });
  }

  private handleMessage(message: CoinbaseWebSocketMessage): void {
    // Check sequence number if present
    if (message.sequence && message.product_id) {
      const lastSequence = this.sequenceNumbers.get(message.product_id) || 0;

      if (message.sequence <= lastSequence) {
        // Old message, ignore
        return;
      }

      if (message.sequence > lastSequence + 1) {
        // Gap detected
        this.emit("sequence_gap", {
          product_id: message.product_id,
          expected: lastSequence + 1,
          received: message.sequence,
        });
      }

      this.sequenceNumbers.set(message.product_id, message.sequence);
    }

    // Route message by type
    switch (message.type) {
      case "ticker":
        this.emit("ticker", message as TickerMessage);
        break;

      case "l2update":
        this.emit("l2update", message as Level2Update);
        break;

      case "heartbeat":
        this.emit("heartbeat", message as HeartbeatMessage);
        break;

      case "error":
        this.emit("error", new Error((message as ErrorMessage).message));
        break;

      case "subscriptions":
        this.emit("subscriptions", message);
        break;

      default:
        this.emit("message", message);
    }

    // Also emit raw message for any listeners
    this.emit("raw_message", message);
  }

  public getConnectionState(): ConnectionState {
    return this.connection.getState();
  }

  // Get all tracked products
  public getTrackedProducts(): string[] {
    return Array.from(this.sequenceNumbers.keys());
  }
}
