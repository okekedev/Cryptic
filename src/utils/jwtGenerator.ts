import { sign } from "jsonwebtoken";
import crypto from "crypto";

export interface JWTConfig {
  apiKey: string; 
  apiSecret: string; 
}


export function generateJWT(config: JWTConfig): string {
  const algorithm = "ES256";

  const payload = {
    iss: "cdp",
    nbf: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + 120, // 2 minutes
    sub: config.apiKey,
  };

  const headers = {
    kid: config.apiKey,
    typ: "JWT",
    nonce: crypto.randomBytes(16).toString("hex"),
  };

  try {
    return sign(payload, config.apiSecret, {
      algorithm,
      header: headers,
    });
  } catch (error) {
    throw new Error(`Failed to generate JWT: ${error.message}`);
  }
}

/**
 * JWT Manager to handle automatic regeneration
 */
export class JWTManager {
  private jwt: string | null = null;
  private jwtExpiry: number = 0;
  private regenerateTimer: NodeJS.Timeout | null = null;

  constructor(private config: JWTConfig) {}

  /**
   * Get a valid JWT, regenerating if necessary
   */
  public getJWT(): string {
    const now = Date.now() / 1000;

    // Regenerate if expired or about to expire (30 seconds buffer)
    if (!this.jwt || now >= this.jwtExpiry - 30) {
      this.regenerate();
    }

    return this.jwt!;
  }

  /**
   * Start automatic JWT regeneration
   */
  public startAutoRegeneration(): void {
    this.stopAutoRegeneration();

    // Regenerate every 90 seconds (JWT lasts 2 minutes, so we have buffer)
    this.regenerateTimer = setInterval(() => {
      this.regenerate();
    }, 90000);

    // Generate initial JWT
    this.regenerate();
  }

  /**
   * Stop automatic JWT regeneration
   */
  public stopAutoRegeneration(): void {
    if (this.regenerateTimer) {
      clearInterval(this.regenerateTimer);
      this.regenerateTimer = null;
    }
  }

  private regenerate(): void {
    try {
      this.jwt = generateJWT(this.config);
      this.jwtExpiry = Math.floor(Date.now() / 1000) + 120;
      console.log(
        "JWT regenerated, expires at:",
        new Date(this.jwtExpiry * 1000).toISOString()
      );
    } catch (error) {
      console.error("Failed to regenerate JWT:", error);
      throw error;
    }
  }
}
