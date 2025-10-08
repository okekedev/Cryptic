#!/usr/bin/env node
/**
 * Test script for ladder buy/sell with SMALL $1 order
 * Only run this manually when you want to test!
 */
const io = require('socket.io-client');

// Connect to backend Socket.IO
const socket = io('http://localhost:5000');

socket.on('connect', () => {
    console.log('✅ Connected to backend');

    // Get current LOKA price for realistic test
    const lokaPrice = 0.24; // Approximate current price

    // Emit dump alert for LOKA-USD with SMALL position
    const alertData = {
        symbol: "LOKA-USD",
        spike_type: "dump",
        pct_change: -5.25,
        old_price: lokaPrice * 1.055, // 5.5% higher
        new_price: lokaPrice,
        time_span_seconds: 300,
        timestamp: new Date().toISOString(),
        spike_time: new Date().toISOString(),
        event_type: "spike_start",
        volume_surge: 2.5
    };

    console.log(`📤 Emitting TEST dump alert for ${alertData.symbol}`);
    console.log(`   Alert price: $${alertData.new_price}`);
    console.log(`   This will trigger a ~$1 order (based on DUMP_POSITION_SIZE_PERCENT=11.43% of $175 capital)`);
    console.log('');
    console.log('⚠️  WARNING: This places a REAL order on Coinbase!');
    console.log('');

    socket.emit('spike_alert', alertData);
    console.log('✅ Alert emitted - check dump-trading logs');

    // Wait a bit then disconnect
    setTimeout(() => {
        socket.disconnect();
        process.exit(0);
    }, 2000);
});

socket.on('disconnect', () => {
    console.log('❌ Disconnected from backend');
});

console.log('🔌 Connecting to backend...');
console.log('📝 This will test the LADDER BUY strategy:');
console.log('   -3% → -2% → -1% → -0.5% → -0.25% (30s each, 2min final)');
console.log('');
