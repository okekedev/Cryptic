const jwt = require('jsonwebtoken');
const crypto = require('crypto');

class JWTTokenManager {
  constructor(apiKey, signingKey) {
    this.apiKey = apiKey;
    // Fix: Replace \n with actual newlines
    this.signingKey = signingKey.replace(/\\n/g, '\n');
    this.currentToken = null;
    this.tokenExpiry = null;
  }

  /**
   * Generate a new JWT token for Coinbase Advanced Trade WebSocket
   * @returns {string} JWT token
   */
  generateToken() {
    try {
      const now = Math.floor(Date.now() / 1000);
      const payload = {
        iss: "cdp",
        nbf: now,
        exp: now + 120, // 2 minutes expiry
        sub: this.apiKey,
      };

      const token = jwt.sign(payload, this.signingKey, {
        algorithm: "ES256",
        header: {
          kid: this.apiKey,
          nonce: crypto.randomBytes(16).toString("hex"),
        },
      });

      this.currentToken = token;
      this.tokenExpiry = (now + 120) * 1000; // Convert to milliseconds

      return token;
    } catch (error) {
      console.error('Failed to generate JWT token:', error.message);
      console.error('Please verify your COINBASE_SIGNING_KEY is properly formatted.');
      throw error;
    }
  }

  /**
   * Get current token, generating a new one if expired or doesn't exist
   * @returns {string} Valid JWT token
   */
  getValidToken() {
    const now = Date.now();

    // Generate new token if we don't have one or it's expired (with 30s buffer)
    if (!this.currentToken || !this.tokenExpiry || now >= (this.tokenExpiry - 30000)) {
      console.log('Generating new JWT token...');
      return this.generateToken();
    }

    return this.currentToken;
  }

  /**
   * Check if current token is expired
   * @returns {boolean} True if token is expired or doesn't exist
   */
  isTokenExpired() {
    if (!this.tokenExpiry) return true;

    const now = Date.now();
    return now >= (this.tokenExpiry - 30000); // 30s buffer
  }

  /**
   * Force token refresh
   * @returns {string} New JWT token
   */
  refreshToken() {
    console.log('Force refreshing JWT token...');
    return this.generateToken();
  }
}

module.exports = JWTTokenManager;