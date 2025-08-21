// src/utils/jwtGenerator.ts

import { sign } from "jsonwebtoken";
import * as crypto from "crypto";

export interface JWTConfig {
  apiKey: string; // organizations/{org_id}/apiKeys/{key_id}
  apiSecret: string; // EC private key with newlines
}

export function generateJWT(config: JWTConfig): string {
  const payload = {
    iss: "cdp",
    nbf: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 120, // Expires in 2 minutes
    sub: config.apiKey,
  };

  const headers = {
    kid: config.apiKey,
    typ: "JWT",
    alg: "ES256",
    nonce: crypto.randomBytes(16).toString("hex"),
  };

  try {
    // The sign function expects (payload, secretOrPrivateKey, options)
    return sign(payload, config.apiSecret, {
      algorithm: "ES256",
      header: headers,
    });
  } catch (error) {
    throw new Error(`Failed to generate JWT: ${(error as Error).message}`);
  }
}

/**
 * JWT Manager to handle automatic regeneration
 * Regenerates JWT before expiration
 */
export class JWTManager {
  private config: JWTConfig;
  private currentJWT: string | null = null;
  private regenerateTimer: NodeJS.Timeout | null = null;
  private onNewJWT: ((jwt: string) => void) | null = null;

  constructor(config: JWTConfig) {
    this.config = config;
  }

  public start(callback: (jwt: string) => void): void {
    this.onNewJWT = callback;
    this.regenerate();
  }

  public stop(): void {
    if (this.regenerateTimer) {
      clearTimeout(this.regenerateTimer);
      this.regenerateTimer = null;
    }
    this.currentJWT = null;
    this.onNewJWT = null;
  }

  public getCurrentJWT(): string | null {
    return this.currentJWT;
  }

  private regenerate(): void {
    try {
      this.currentJWT = generateJWT(this.config);

      if (this.onNewJWT) {
        this.onNewJWT(this.currentJWT);
      }

      // Schedule next regeneration 10 seconds before expiration (110 seconds)
      this.regenerateTimer = setTimeout(() => {
        this.regenerate();
      }, 110000);
    } catch (error) {
      console.error("Failed to regenerate JWT:", error);
      // Retry in 5 seconds on failure
      this.regenerateTimer = setTimeout(() => {
        this.regenerate();
      }, 5000);
    }
  }
}

/**
 * Helper function to add JWT to WebSocket messages
 */
export function addJWTToMessage(message: any, jwt: string): any {
  return {
    ...message,
    jwt: jwt,
  };
}
