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
      // Validate signing key format
      if (!this.signingKey) {
        throw new Error('COINBASE_SIGNING_KEY is not provided');
      }

      if (!this.signingKey.includes('-----BEGIN EC PRIVATE KEY-----')) {
        throw new Error('COINBASE_SIGNING_KEY must start with "-----BEGIN EC PRIVATE KEY-----"');
      }

      if (!this.signingKey.includes('-----END EC PRIVATE KEY-----')) {
        throw new Error('COINBASE_SIGNING_KEY must end with "-----END EC PRIVATE KEY-----"');
      }

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

      console.log('JWT token generated successfully');
      return token;
    } catch (error) {
      console.error('Failed to generate JWT token:', error.message);

      if (error.message.includes('secretOrPrivateKey must be an asymmetric key')) {
        console.error('');
        console.error('🔑 JWT Token Error: Invalid private key format');
        console.error('Your COINBASE_SIGNING_KEY in .env must be formatted like this:');
        console.error('COINBASE_SIGNING_KEY=-----BEGIN EC PRIVATE KEY-----\\nYOUR_KEY_CONTENT\\n-----END EC PRIVATE KEY-----');
        console.error('');
        console.error('Make sure:');
        console.error('1. Key starts with -----BEGIN EC PRIVATE KEY-----');
        console.error('2. Key ends with -----END EC PRIVATE KEY-----');
        console.error('3. Use \\n for newlines (not actual newlines)');
        console.error('4. No extra spaces around the = sign');
        console.error('');
      }

      return null; // Return null instead of throwing to allow graceful handling
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
      const newToken = this.generateToken();
      if (!newToken) {
        console.error('Failed to generate valid JWT token');
        return null;
      }
      return newToken;
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
    const newToken = this.generateToken();
    if (!newToken) {
      console.error('Failed to refresh JWT token');
      return null;
    }
    return newToken;
  }
}

module.exports = JWTTokenManager;