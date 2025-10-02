#!/usr/bin/env node
/**
 * Test Coinbase Connection and Execute $1 BTC Test Trade
 *
 * This script will:
 * 1. Check your USD wallet balance
 * 2. Place a $1 market buy order for BTC-USD
 * 3. Verify the order was executed
 */

const crypto = require('crypto');
const https = require('https');

// Load environment variables from .env file
require('dotenv').config({ path: '../.env' });

const API_KEY = process.env.COINBASE_API_KEY;
const SIGNING_KEY = process.env.COINBASE_SIGNING_KEY;

if (!API_KEY || !SIGNING_KEY) {
    console.error('❌ Error: COINBASE_API_KEY and COINBASE_SIGNING_KEY must be set in .env file');
    process.exit(1);
}

// Clean up signing key (remove escaped newlines)
const cleanSigningKey = SIGNING_KEY.replace(/\\n/g, '\n');

/**
 * Generate JWT token for Coinbase API authentication
 */
function generateJWT(method, path) {
    const jwt = require('jsonwebtoken');

    const uri = `${method} api.coinbase.com${path}`;
    const currentTime = Math.floor(Date.now() / 1000);

    const payload = {
        sub: API_KEY,
        iss: 'coinbase-cloud',
        nbf: currentTime,
        exp: currentTime + 120,
        uri: uri
    };

    const token = jwt.sign(payload, cleanSigningKey, {
        algorithm: 'ES256',
        header: {
            kid: API_KEY,
            nonce: currentTime.toString()
        }
    });

    return token;
}

/**
 * Make authenticated request to Coinbase API
 */
function makeRequest(method, path, body = null) {
    return new Promise((resolve, reject) => {
        const token = generateJWT(method, path);

        const options = {
            hostname: 'api.coinbase.com',
            path: path,
            method: method,
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
            }
        };

        const req = https.request(options, (res) => {
            let data = '';

            res.on('data', (chunk) => {
                data += chunk;
            });

            res.on('end', () => {
                try {
                    const parsed = JSON.parse(data);
                    if (res.statusCode === 200) {
                        resolve(parsed);
                    } else {
                        reject({ statusCode: res.statusCode, error: parsed });
                    }
                } catch (e) {
                    reject({ error: 'Failed to parse response', raw: data });
                }
            });
        });

        req.on('error', (e) => {
            reject(e);
        });

        if (body) {
            req.write(JSON.stringify(body));
        }

        req.end();
    });
}

/**
 * Get USD wallet balance
 */
async function getUSDBalance() {
    console.log('\n📊 Fetching USD wallet balance...');

    try {
        const response = await makeRequest('GET', '/api/v3/brokerage/accounts');

        const accounts = response.accounts || [];
        const usdAccount = accounts.find(acc => acc.currency === 'USD');

        if (!usdAccount) {
            console.error('❌ No USD account found');
            return null;
        }

        const balance = parseFloat(usdAccount.available_balance?.value || 0);
        console.log(`✅ USD Balance: $${balance.toFixed(2)}`);

        return balance;
    } catch (error) {
        console.error('❌ Error fetching balance:', error);
        return null;
    }
}

/**
 * Place a $1 market buy order for BTC-USD
 */
async function placeBTCOrder() {
    console.log('\n🔄 Placing $1 market buy order for BTC-USD...');

    const clientOrderId = `test_buy_${Date.now()}`;

    const orderData = {
        client_order_id: clientOrderId,
        product_id: 'BTC-USD',
        side: 'BUY',
        order_configuration: {
            market_market_ioc: {
                quote_size: '1.00'
            }
        }
    };

    console.log('\n📤 Order details:');
    console.log(JSON.stringify(orderData, null, 2));

    try {
        const response = await makeRequest('POST', '/api/v3/brokerage/orders', orderData);

        console.log('\n📥 Coinbase API Response:');
        console.log(JSON.stringify(response, null, 2));

        // Extract order_id
        let orderId = null;
        if (response.success && response.success_response) {
            orderId = response.success_response.order_id;
        } else if (response.order_id) {
            orderId = response.order_id;
        }

        if (orderId) {
            console.log(`\n✅ Order placed successfully!`);
            console.log(`   Order ID: ${orderId}`);
            console.log(`   Client Order ID: ${clientOrderId}`);
            return orderId;
        } else {
            console.error('❌ Failed to extract order_id from response');
            return null;
        }
    } catch (error) {
        console.error('\n❌ Error placing order:', error);

        if (error.error) {
            console.error('Error details:', JSON.stringify(error.error, null, 2));
        }

        return null;
    }
}

/**
 * Get order details
 */
async function getOrderDetails(orderId) {
    console.log(`\n🔍 Fetching order details for ${orderId}...`);

    try {
        const response = await makeRequest('GET', `/api/v3/brokerage/orders/historical/${orderId}`);

        console.log('\n📊 Order Details:');
        console.log(JSON.stringify(response, null, 2));

        if (response.order) {
            const order = response.order;
            console.log(`\n✅ Order Status: ${order.status}`);
            console.log(`   Product: ${order.product_id}`);
            console.log(`   Side: ${order.side}`);
            console.log(`   Size: ${order.filled_size || order.order_configuration?.market_market_ioc?.quote_size || 'N/A'}`);

            if (order.average_filled_price) {
                console.log(`   Average Price: $${parseFloat(order.average_filled_price).toFixed(2)}`);
            }
        }

        return response;
    } catch (error) {
        console.error('❌ Error fetching order details:', error);
        return null;
    }
}

/**
 * Main test function
 */
async function runTest() {
    console.log('\n' + '='.repeat(60));
    console.log('🧪 COINBASE CONNECTION TEST');
    console.log('='.repeat(60));

    // Step 1: Check balance
    const balance = await getUSDBalance();

    if (balance === null) {
        console.error('\n❌ Test failed: Could not fetch balance');
        process.exit(1);
    }

    console.log('\n' + '='.repeat(60));
    console.log('✅ CONNECTION TEST COMPLETE');
    console.log('='.repeat(60));
    console.log('\n📝 Summary:');
    console.log('   1. ✅ Successfully connected to Coinbase API');
    console.log('   2. ✅ Retrieved USD wallet balance');
    console.log('\n💡 Note: Auto-trading functionality disabled in this script');
    console.log('   (Previously placed test orders - now only checks balance)');
    console.log('\n');
}

// Run the test
runTest().catch(error => {
    console.error('\n❌ Fatal error:', error);
    process.exit(1);
});
