import { EventEmitter } from "events";
import WebSocket from "ws";

export type ConnectionState =
  | "DISCONNECTED"
  | "CONNECTING"
  | "CONNECTED"
  | "RECONNECTING";

export interface WebSocketConfig {
  url: string;
  reconnect?: boolean;
  reconnectInterval?: number;
  maxReconnectAttempts?: number;
  pingInterval?: number;
}

export class WebSocketConnection extends EventEmitter {
  private ws: WebSocket | null = null;
  private config: WebSocketConfig;
  private state: ConnectionState = "DISCONNECTED";
  private reconnectAttempts = 0;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private pingTimer: NodeJS.Timeout | null = null;
  private shouldReconnect = true;

  constructor(config: WebSocketConfig) {
    super();
    this.config = {
      reconnect: true,
      reconnectInterval: 1000,
      maxReconnectAttempts: Infinity,
      pingInterval: 30000,
      ...config,
    };
  }

  public connect(): void {
    if (this.state === "CONNECTED" || this.state === "CONNECTING") {
      return;
    }

    this.shouldReconnect = true;
    this.state = "CONNECTING";
    this.emit("connecting");

    try {
      this.ws = new WebSocket(this.config.url);
      this.setupEventHandlers();
    } catch (error) {
      this.handleError(error);
    }
  }

  public disconnect(): void {
    this.shouldReconnect = false;
    this.clearTimers();

    if (this.ws) {
      this.ws.removeAllListeners();

      if (this.ws.readyState === WebSocket.OPEN) {
        this.ws.close(1000, "Normal closure");
      } else {
        this.ws.terminate();
      }

      this.ws = null;
    }

    this.state = "DISCONNECTED";
    this.emit("disconnected", { reason: "User requested disconnect" });
  }

  public send(data: any): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error("WebSocket is not connected");
    }

    const message = typeof data === "string" ? data : JSON.stringify(data);
    this.ws.send(message);
  }

  public getState(): ConnectionState {
    return this.state;
  }

  private setupEventHandlers(): void {
    if (!this.ws) return;

    this.ws.on("open", () => {
      this.state = "CONNECTED";
      this.reconnectAttempts = 0;
      this.emit("connected");
      this.startPing();
    });

    this.ws.on("message", (data: WebSocket.Data) => {
      try {
        const message = JSON.parse(data.toString());
        this.emit("message", message);
      } catch (error) {
        // If not JSON, emit raw message
        this.emit("raw_message", data.toString());
      }
    });

    this.ws.on("error", (error: Error) => {
      this.handleError(error);
    });

    this.ws.on("close", (code: number, reason: string) => {
      this.handleClose(code, reason);
    });

    this.ws.on("pong", () => {
      // Keep-alive received
      this.emit("pong");
    });
  }

  private handleError(error: any): void {
    this.emit("error", error);

    // Don't try to reconnect if we're already disconnecting
    if (!this.shouldReconnect) {
      return;
    }

    // WebSocket errors often lead to close events
    // Let the close handler deal with reconnection
  }

  private handleClose(code: number, reason: string): void {
    this.clearTimers();

    const wasConnected = this.state === "CONNECTED";
    this.state = "DISCONNECTED";

    this.emit("disconnected", { code, reason });

    // Attempt reconnection if enabled and not manually disconnected
    if (
      this.shouldReconnect &&
      this.config.reconnect &&
      this.reconnectAttempts < (this.config.maxReconnectAttempts || Infinity)
    ) {
      this.scheduleReconnect();
    } else if (
      this.reconnectAttempts >= (this.config.maxReconnectAttempts || Infinity)
    ) {
      this.emit("max_reconnect_attempts_reached");
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) {
      return;
    }

    this.state = "RECONNECTING";
    this.reconnectAttempts++;

    const delay = this.getReconnectDelay();

    this.emit("reconnecting", {
      attempt: this.reconnectAttempts,
      delay,
      maxAttempts: this.config.maxReconnectAttempts,
    });

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  private getReconnectDelay(): number {
    // Exponential backoff with jitter
    const baseDelay = this.config.reconnectInterval || 1000;
    const exponentialDelay = Math.min(
      baseDelay * Math.pow(2, this.reconnectAttempts - 1),
      30000
    );
    const jitter = Math.random() * 0.3 * exponentialDelay; // 30% jitter
    return exponentialDelay + jitter;
  }

  private startPing(): void {
    if (!this.config.pingInterval || this.pingTimer) {
      return;
    }

    this.pingTimer = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.ping();
      }
    }, this.config.pingInterval);
  }

  private clearTimers(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }
}
