import os
import time
import numpy as np
from collections import deque, defaultdict
from datetime import datetime, timedelta
from typing import Dict, Deque, Optional, Tuple, List
import logging

logger = logging.getLogger(__name__)

class EnhancedPriceTracker:
    
    def __init__(self, symbol: str, spike_threshold: float = 5.0):
        self.symbol = symbol
        self.spike_threshold = spike_threshold
        
        # Multi-timeframe windows (in seconds)
        self.timeframes = {
            '1m': 60,
            '5m': 300,
            '15m': 900,
            '30m': 1800,
            '1h': 3600
        }
        
        # Price history for each timeframe
        self.price_histories: Dict[str, Deque[Tuple[float, float]]] = {}
        for tf in self.timeframes:
            self.price_histories[tf] = deque(maxlen=1000)
        
        # Tracking variables
        self.last_price = 0
        self.last_spike_alert = 0
        self.cooldown_seconds = 60
        
        # Cumulative tracking
        self.local_low_price = float('inf')
        self.local_low_time = 0
        self.cumulative_gain_start = None
        
        # Volume weighted price tracking (optional)
        self.vwap_window = deque(maxlen=100)  # (price, volume, timestamp)
        
        # Momentum tracking (from original)
        self.momentum_tracking = False
        self.momentum_start_price = 0
        self.momentum_peak_price = 0
        self.momentum_start_time = 0
        self.peak_change = 0
        
        # Dynamic threshold based on volatility
        self.atr_window = deque(maxlen=20)  # Average True Range
        self.dynamic_threshold_enabled = True
        self.min_threshold = 3.0  # Minimum threshold even in low volatility
        self.max_threshold = 10.0  # Maximum threshold in high volatility

    def add_price(self, price: float, timestamp: float, volume: float = 1.0) -> Optional[Dict]:
        """Add price and check for spikes across multiple timeframes"""
        
        # Update all timeframe histories
        for tf_name, window_seconds in self.timeframes.items():
            history = self.price_histories[tf_name]
            history.append((timestamp, price))
            
            # Clean old entries
            cutoff_time = timestamp - window_seconds
            while history and history[0][0] < cutoff_time:
                history.popleft()
        
        # Track local lows for cumulative gains
        if price < self.local_low_price:
            self.local_low_price = price
            self.local_low_time = timestamp
        
        # Update ATR for dynamic thresholds
        if self.last_price > 0:
            self.atr_window.append(abs(price - self.last_price) / self.last_price * 100)
        
        # Update VWAP data
        self.vwap_window.append((price, volume, timestamp))
        
        # Check for spikes
        spike = self._check_for_spikes(price, timestamp, volume)
        
        self.last_price = price
        return spike

    def _check_for_spikes(self, price: float, timestamp: float, volume: float) -> Optional[Dict]:
        """Check for price spikes across multiple timeframes"""
        
        # If in momentum tracking mode
        if self.momentum_tracking:
            return self._track_momentum(price, timestamp)
        
        # Calculate dynamic threshold if enabled
        threshold = self._calculate_dynamic_threshold()
        
        # Check each timeframe for spikes
        spike_results = []
        
        for tf_name, history in self.price_histories.items():
            if len(history) < 2:
                continue
                
            oldest_time, oldest_price = history[0]
            
            if oldest_price == 0:
                continue
            
            # Calculate percentage change
            pct_change = ((price - oldest_price) / oldest_price) * 100
            
            # Rolling window check - find the max gain within the window
            max_gain = 0
            min_price_in_window = oldest_price
            
            for ts, p in history:
                if p < min_price_in_window:
                    min_price_in_window = p
                current_gain = ((price - min_price_in_window) / min_price_in_window) * 100
                if current_gain > max_gain:
                    max_gain = current_gain
            
            spike_results.append({
                'timeframe': tf_name,
                'pct_change': pct_change,
                'max_gain': max_gain,
                'window_start': oldest_time,
                'start_price': oldest_price,
                'min_price': min_price_in_window
            })
        
        # Check cumulative gain from recent low
        cumulative_gain = ((price - self.local_low_price) / self.local_low_price) * 100 if self.local_low_price < float('inf') else 0
        
        # Determine if we should trigger an alert
        best_spike = None
        for result in spike_results:
            if abs(result['max_gain']) >= threshold or abs(result['pct_change']) >= threshold:
                if not best_spike or abs(result['max_gain']) > abs(best_spike.get('max_gain', 0)):
                    best_spike = result
        
        # Also check cumulative gain
        if cumulative_gain >= threshold and (timestamp - self.local_low_time) < 3600:  # Within last hour
            if not best_spike or cumulative_gain > abs(best_spike.get('max_gain', 0)):
                best_spike = {
                    'timeframe': 'cumulative',
                    'pct_change': cumulative_gain,
                    'max_gain': cumulative_gain,
                    'window_start': self.local_low_time,
                    'start_price': self.local_low_price,
                    'min_price': self.local_low_price
                }
        
        # If we found a significant spike and cooldown has passed
        if best_spike and (timestamp - self.last_spike_alert) > self.cooldown_seconds:
            self.last_spike_alert = timestamp
            
            # Start momentum tracking
            self.momentum_tracking = True
            self.momentum_start_price = best_spike['start_price']
            self.momentum_peak_price = price
            self.momentum_start_time = best_spike['window_start']
            self.peak_change = best_spike['max_gain']
            
            # Calculate volume-weighted average if available
            vwap = self._calculate_vwap()
            
            return {
                "symbol": self.symbol,
                "spike_type": "pump" if best_spike['max_gain'] > 0 else "dump",
                "pct_change": best_spike['max_gain'],
                "timeframe": best_spike['timeframe'],
                "old_price": best_spike['start_price'],
                "new_price": price,
                "min_price_in_window": best_spike['min_price'],
                "cumulative_gain": cumulative_gain,
                "threshold_used": threshold,
                "vwap": vwap,
                "time_span_seconds": timestamp - best_spike['window_start'],
                "timestamp": datetime.fromtimestamp(timestamp).isoformat(),
                "event_type": "spike_start",
                "all_timeframe_results": spike_results
            }
        
        return None

    def _calculate_dynamic_threshold(self) -> float:
        """Calculate dynamic threshold based on recent volatility"""
        if not self.dynamic_threshold_enabled or len(self.atr_window) < 5:
            return self.spike_threshold
        
        # Calculate average volatility
        avg_volatility = np.mean(list(self.atr_window))
        
        # Scale threshold based on volatility
        # Low volatility = lower threshold, high volatility = higher threshold
        if avg_volatility < 0.5:  # Very low volatility
            dynamic_threshold = self.min_threshold
        elif avg_volatility > 2.0:  # High volatility
            dynamic_threshold = self.max_threshold
        else:
            # Linear scaling between min and max
            scale = (avg_volatility - 0.5) / 1.5
            dynamic_threshold = self.min_threshold + (self.max_threshold - self.min_threshold) * scale
        
        return dynamic_threshold

    def _calculate_vwap(self) -> Optional[float]:
        """Calculate volume-weighted average price"""
        if not self.vwap_window:
            return None
        
        total_volume = sum(v for _, v, _ in self.vwap_window)
        if total_volume == 0:
            return None
        
        vwap = sum(p * v for p, v, _ in self.vwap_window) / total_volume
        return vwap

    def _track_momentum(self, current_price: float, timestamp: float) -> Optional[Dict]:
        """Track momentum after initial spike (from original implementation)"""
        current_change = ((current_price - self.momentum_start_price) / self.momentum_start_price) * 100
        
        # Update peak if still climbing
        if abs(current_change) > abs(self.peak_change):
            self.momentum_peak_price = current_price
            self.peak_change = current_change
        
        # Check if momentum has ended
        exit_threshold = self.spike_threshold - 2.0
        momentum_ended = False
        
        if self.peak_change > 0:  # Pump
            momentum_ended = current_change < exit_threshold
        else:  # Dump
            momentum_ended = current_change > -exit_threshold
        
        if momentum_ended:
            # Calculate final statistics
            duration = timestamp - self.momentum_start_time
            
            result = {
                "symbol": self.symbol,
                "spike_type": "pump" if self.peak_change > 0 else "dump",
                "pct_change": self.peak_change,
                "old_price": self.momentum_start_price,
                "new_price": current_price,
                "peak_price": self.momentum_peak_price,
                "peak_change": self.peak_change,
                "final_change": current_change,
                "time_span_seconds": duration,
                "timestamp": datetime.fromtimestamp(timestamp).isoformat(),
                "event_type": "momentum_end"
            }
            
            # Reset momentum tracking
            self.momentum_tracking = False
            self.momentum_start_price = 0
            self.momentum_peak_price = 0
            self.momentum_start_time = 0
            self.peak_change = 0
            
            # Reset local low tracking after significant move
            if abs(self.peak_change) > 10:
                self.local_low_price = current_price
                self.local_low_time = timestamp
            
            return result
        
        return None

    def get_current_metrics(self) -> Dict:
        """Get current tracking metrics for monitoring"""
        metrics = {
            "symbol": self.symbol,
            "last_price": self.last_price,
            "momentum_active": self.momentum_tracking,
            "local_low": self.local_low_price if self.local_low_price < float('inf') else None,
            "timeframe_counts": {tf: len(hist) for tf, hist in self.price_histories.items()},
            "current_volatility": np.mean(list(self.atr_window)) if self.atr_window else 0,
            "dynamic_threshold": self._calculate_dynamic_threshold() if self.dynamic_threshold_enabled else self.spike_threshold
        }
        return metrics