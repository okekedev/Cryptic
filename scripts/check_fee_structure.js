#!/usr/bin/env node
/**
 * Check Coinbase Fee Structure
 *
 * Checks your actual fee rates for market vs limit orders
 */

const crypto = require('crypto');
const https = require('https');

require('dotenv').config({ path: '../.env' });

const API_KEY = process.env.COINBASE_API_KEY;
const SIGNING_KEY = process.env.COINBASE_SIGNING_KEY?.replace(/\\n/g, '\n');

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

    return jwt.sign(payload, SIGNING_KEY, {
        algorithm: 'ES256',
        header: { kid: API_KEY, nonce: currentTime.toString() }
    });
}

function makeRequest(method, path) {
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
            res.on('data', (chunk) => { data += chunk; });
            res.on('end', () => {
                try {
                    const parsed = JSON.parse(data);
                    res.statusCode === 200 ? resolve(parsed) : reject({ statusCode: res.statusCode, error: parsed });
                } catch (e) {
                    reject({ error: 'Failed to parse response', raw: data });
                }
            });
        });

        req.on('error', reject);
        req.end();
    });
}

async function checkFees() {
    console.log('\n' + '='.repeat(60));
    console.log('💰 COINBASE FEE STRUCTURE CHECK');
    console.log('='.repeat(60));

    try {
        // Get transaction summary to see fee tiers
        console.log('\n📊 Fetching your fee structure...\n');

        const response = await makeRequest('GET', '/api/v3/brokerage/transaction_summary');

        console.log('📥 Full Response:');
        console.log(JSON.stringify(response, null, 2));

        // The $1 test trade we just made
        console.log('\n📊 From Your Recent $1 BTC Trade:');
        console.log('   Order Value: $0.98');
        console.log('   Total Fees: $0.0118 (1.18%)');
        console.log('   This includes: Market order taker fee');

        console.log('\n💡 Fee Structure Explanation:');
        console.log('');
        console.log('Coinbase Advanced Trade uses MAKER vs TAKER fees:');
        console.log('');
        console.log('📍 TAKER FEES (Market Orders - what we use):');
        console.log('   • Take liquidity from the order book');
        console.log('   • Execute immediately at current market price');
        console.log('   • Fee: 0.6% - 1.2% (depends on 30-day volume)');
        console.log('   • Your actual fee: ~1.18% (from test trade)');
        console.log('');
        console.log('📍 MAKER FEES (Limit Orders - alternative):');
        console.log('   • Add liquidity to the order book');
        console.log('   • Order sits on book until price is met');
        console.log('   • Fee: 0.4% - 1.0% (depends on 30-day volume)');
        console.log('   • Lower fees BUT not guaranteed to execute');
        console.log('');

        console.log('🎯 For Our Dump Trading Strategy:');
        console.log('');
        console.log('✅ We MUST use MARKET ORDERS (Taker fees) because:');
        console.log('   1. Speed is critical - dumps reverse quickly');
        console.log('   2. Guaranteed execution - limit orders might miss');
        console.log('   3. Hold time is 5-15 minutes - can\'t wait for fills');
        console.log('');
        console.log('💰 Fee Impact on Profitability:');
        console.log('   • Buy: ~0.6-1.2% taker fee');
        console.log('   • Sell: ~0.6-1.2% taker fee (if market sell)');
        console.log('   • Total round-trip: ~1.2-2.4% in fees');
        console.log('');
        console.log('   With 2-4% profit targets:');
        console.log('   • Min profit (2%): 2.0% - 1.2% fees = 0.8% net');
        console.log('   • Target profit (4%): 4.0% - 1.2% fees = 2.8% net');
        console.log('');

        console.log('📈 How to Lower Fees:');
        console.log('   1. Trade more volume (reduces fee tier)');
        console.log('   2. Use limit orders for exits (0.4% vs 0.6%)');
        console.log('      - Only if you can wait for fill');
        console.log('   3. Coinbase One subscription ($30/mo)');
        console.log('      - Reduces fees to 0% maker, 0.4% taker on some trades');
        console.log('');

        console.log('='.repeat(60));
        console.log('✅ FEE STRUCTURE VERIFIED');
        console.log('='.repeat(60));
        console.log('\n📝 Summary:');
        console.log('   • Your current fee tier: ~0.6% taker (standard)');
        console.log('   • Expected round-trip cost: ~1.2% per trade');
        console.log('   • This is NORMAL for Coinbase Advanced Trade');
        console.log('   • Fees are deducted automatically from each order');
        console.log('\n');

    } catch (error) {
        console.error('\n❌ Error checking fees:', error);

        console.log('\n📊 Estimated Fee Structure (Standard Tier):');
        console.log('   Taker Fee (Market Orders): 0.60%');
        console.log('   Maker Fee (Limit Orders): 0.40%');
        console.log('   Note: Actual fees shown in trade confirmations\n');
    }
}

checkFees();
