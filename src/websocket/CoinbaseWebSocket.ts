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
  SubscriptionsMessage,
} from "../types/coinbase";

export interface CoinbaseConfig {
  channels: string[];
  productIds: string[];
}

export class CoinbaseWebSocket extends EventEmitter {
  private connection: WebSocketConnection;
  private sequenceNumbers: Map<string, number> = new Map();
  private config: CoinbaseConfig;
  private activeSubscriptions: Map<string, Set<string>> = new Map();
  private pendingSubscriptions: Array<{
    channel: string;
    productIds: string[];
  }> = [];
  private isConnected = false;

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
    this.isConnected = false;
    this.connection.disconnect();
  }

  public subscribe(channel: string, productIds: string[]): void {
    // If not connected yet, queue the subscription
    if (!this.isConnected) {
      this.pendingSubscriptions.push({ channel, productIds });
      return;
    }

    const subscribeMessage: SubscriptionMessage = {
      type: "subscribe",
      channel: channel,
      product_ids: productIds,
    };

    // Track the subscription
    if (!this.activeSubscriptions.has(channel)) {
      this.activeSubscriptions.set(channel, new Set());
    }
    const channelProducts = this.activeSubscriptions.get(channel)!;
    productIds.forEach((productId) => channelProducts.add(productId));

    console.log(`Subscribing to ${channel} with products:`, productIds);
    this.connection.send(subscribeMessage);
  }

  public unsubscribe(channel: string, productIds: string[]): void {
    if (!this.isConnected) {
      return;
    }

    const unsubscribeMessage: SubscriptionMessage = {
      type: "unsubscribe",
      channel: channel,
      product_ids: productIds,
    };

    // Update tracking
    const channelProducts = this.activeSubscriptions.get(channel);
    if (channelProducts) {
      productIds.forEach((productId) => channelProducts.delete(productId));
      if (channelProducts.size === 0) {
        this.activeSubscriptions.delete(channel);
      }
    }

    this.connection.send(unsubscribeMessage);
  }

  public getSubscribedChannels(): string[] {
    return Array.from(this.activeSubscriptions.keys());
  }

  public getSubscribedProducts(channel: string): string[] {
    const products = this.activeSubscriptions.get(channel);
    return products ? Array.from(products) : [];
  }

  public getActiveSubscriptions(): Map<string, Set<string>> {
    return new Map(this.activeSubscriptions);
  }

  private setupEventHandlers(): void {
    this.connection.on("connected", () => {
      console.log("WebSocket connected to Coinbase");
      this.isConnected = true;
      this.emit("connected");

      // First subscribe to heartbeat to keep connection alive
      this.subscribe("heartbeats", []);

      // Process any pending subscriptions
      setTimeout(() => {
        this.processPendingSubscriptions();
      }, 100);
    });

    this.connection.on("disconnected", (data) => {
      this.isConnected = false;
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
      this.activeSubscriptions.clear();
    });
  }

  private processPendingSubscriptions(): void {
    // Process pending subscriptions
    const pending = [...this.pendingSubscriptions];
    this.pendingSubscriptions = [];

    for (const sub of pending) {
      this.subscribe(sub.channel, sub.productIds);
    }

    // Auto-subscribe to configured channels if any
    if (this.config.channels.length && this.config.productIds.length) {
      for (const channel of this.config.channels) {
        if (!this.activeSubscriptions.has(channel)) {
          this.subscribe(channel, this.config.productIds);
        }
      }
    }
  }

  private handleMessage(message: CoinbaseWebSocketMessage): void {
    // Check sequence number if present
    if (message.sequence !== undefined && message.product_id) {
      const lastSequence = this.sequenceNumbers.get(message.product_id) || 0;

      if (message.sequence <= lastSequence) {
        return; // Old message, ignore
      }

      if (message.sequence > lastSequence + 1) {
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
        const errorMsg = message as ErrorMessage;
        console.error("Coinbase WebSocket error message:", errorMsg);
        this.emit(
          "error",
          new Error(errorMsg.message || errorMsg.reason || "Unknown error")
        );
        break;

      case "subscriptions":
        this.updateSubscriptionsFromMessage(message as SubscriptionsMessage);
        this.emit("subscriptions", message);
        break;

      default:
        this.emit("message", message);
    }

    this.emit("raw_message", message);
  }

  private updateSubscriptionsFromMessage(message: SubscriptionsMessage): void {
    this.activeSubscriptions.clear();

    if (message.channels) {
      message.channels.forEach((channelInfo) => {
        const channelName = channelInfo.name;
        const products = new Set(channelInfo.product_ids);
        this.activeSubscriptions.set(channelName, products);
      });
    }
  }

  public getConnectionState(): ConnectionState {
    return this.connection.getState();
  }

  public getTrackedProducts(): string[] {
    return Array.from(this.sequenceNumbers.keys());
  }
}
