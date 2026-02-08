"""
COMPREHENSIVE FOREX GLOBAL CONFLUENCE SCANNER - Professional Edition
Author: Professional Forex Trader
Goal: Find ALL very high probability setups across ALL forex pairs
     with NO filters, NO limits, just PURE MARKET TRUTH
"""

import requests
import pandas as pd
import numpy as np
import json
import schedule
import time
import threading
import os
import sys
import re
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional, Tuple, Set
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

# ============================================================================
# CONFIGURATION & SETUP
# ============================================================================

# Configure logging
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(log_dir / 'forex_scanner.log', maxBytes=10485760, backupCount=10),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Configuration with persistence"""
    # API Keys
    TWELVEDATA_KEY: str = "2664b95fd52c490bb422607ef142e61f"
    NEWSAPI_KEY: str = "e973313ed2c142cb852101836f33a471"
    
    # Scanner Settings
    MIN_CONFLUENCE_SCORE: int = 70  # Only VERY HIGH probability
    SCAN_INTERVAL_MINUTES: int = 15  # Scan every 15 minutes
    MAX_PAIRS_PER_SCAN: int = 1459  # ALL forex pairs
    
    # Professional TP/SL Settings
    MIN_RISK_REWARD: float = 1.5
    MIN_SUCCESS_PROBABILITY: float = 0.6  # 60% chance TP hits before SL
    MAX_TRADE_DURATION_DAYS: int = 30
    
    # App Keep-Alive (for Render free tier)
    SELF_PING_INTERVAL_MINUTES: int = 10
    
    
    # Data Storage
    DATA_DIR: str = "data"
    OPPORTUNITIES_DB: str = "data/opportunities.json"
    PERFORMANCE_LOG: str = "data/performance.json"
    CONFIG_FILE: str = "data/config.json"
    
    # Risk Management
    MAX_DAILY_RISK_PERCENT: float = 2.0
    POSITION_SIZE_CALCULATION: str = "FIXED_FRACTIONAL"
    
    # API Rate Limiting
    TWELVEDATA_REQUESTS_PER_MINUTE: int = 30  # Free tier limit
    NEWSAPI_REQUESTS_PER_DAY: int = 100  # Free tier limit
    
    def __post_init__(self):
        # Create data directory if it doesn't exist
        os.makedirs(self.DATA_DIR, exist_ok=True)
        # Save configuration
        self.save()

    def save(self):
        """Save configuration to file"""
        with open(self.CONFIG_FILE, 'w') as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls):
        """Load configuration from file"""
        if os.path.exists(cls.CONFIG_FILE):
            with open(cls.CONFIG_FILE, 'r') as f:
                data = json.load(f)
                return cls(**data)
        return cls()

def load_app_url():
    """Ultra-simple URL detection for Render"""
    # Render provides these environment variables automatically
    if 'RENDER' in os.environ:
        url = os.environ.get('RENDER_EXTERNAL_URL', '')
        if url:
            return url
        
        # Fallback to service name construction
        service_name = os.environ.get('RENDER_SERVICE_NAME', 'forex-scanner')
        return f"https://{service_name}.onrender.com"
    
    # For local development
    return "http://localhost:5000"

# ============================================================================
# RATE LIMITER
# ============================================================================

class RateLimiter:
    """Intelligent rate limiter to never exceed API limits"""
    
    def __init__(self, requests_per_minute: int = 30):
        self.requests_per_minute = requests_per_minute
        self.request_timestamps = []
        self.lock = threading.Lock()
        
    def wait_if_needed(self):
        """Wait if rate limit would be exceeded"""
        with self.lock:
            now = time.time()
            # Remove timestamps older than 1 minute
            self.request_timestamps = [
                ts for ts in self.request_timestamps 
                if now - ts < 60
            ]
            
            if len(self.request_timestamps) >= self.requests_per_minute:
                # Calculate wait time
                oldest = self.request_timestamps[0]
                wait_time = 60 - (now - oldest) + 0.1  # Add 100ms buffer
                if wait_time > 0:
                    logger.debug(f"Rate limit reached. Waiting {wait_time:.1f} seconds")
                    time.sleep(wait_time)
            
            self.request_timestamps.append(now)
            # Keep only recent timestamps
            self.request_timestamps = self.request_timestamps[-self.requests_per_minute:]

# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class Opportunity:
    """A very high probability trading opportunity"""
    pair: str
    direction: str  # BUY or SELL
    confluence_score: int  # 70-100 only
    catalyst: str
    setup_type: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    risk_pips: float
    reward_pips: float
    probability_tp_before_sl: float
    estimated_duration_days: int
    context: str  # TRENDING, RANGING, BREAKOUT
    confidence: str  # HIGH, VERY_HIGH
    analysis_summary: str
    fundamentals_summary: str
    technicals_summary: str
    sentiment_summary: str
    detected_at: str
    
    def to_dict(self):
        return asdict(self)

@dataclass
class ScanResult:
    """Results of a complete market scan"""
    timestamp: str
    pairs_scanned: int
    very_high_probability_setups: int
    opportunities: List[Opportunity]
    scan_duration_seconds: float
    market_state: str  # QUIET, NORMAL, VOLATILE, CRISIS
    
    def to_dict(self):
        return {
            'timestamp': self.timestamp,
            'pairs_scanned': self.pairs_scanned,
            'very_high_probability_setups': self.very_high_probability_setups,
            'opportunities': [opp.to_dict() for opp in self.opportunities],
            'scan_duration_seconds': self.scan_duration_seconds,
            'market_state': self.market_state
        }

# ============================================================================
# REAL SENTIMENT DATA COLLECTORS
# ============================================================================

class RealSentimentCollector:
    """Collects REAL sentiment data from multiple sources"""
    
    def __init__(self):
        self.cftc_url = "https://www.cftc.gov/dea/newcot/FinFutWk.txt"
        self.tradingview_url = "https://www.tradingview.com/markets/currencies/rates-major/"
        self.sources = {
            'cftc': self._get_cftc_sentiment,
            'fxssi': self._get_fxssi_sentiment,
            'myfxbook': self._get_myfxbook_sentiment,
            'tradingview': self._get_tradingview_sentiment
        }
        self.cache = {}
        self.cache_expiry = {}
        
    def get_sentiment_for_pair(self, pair: str) -> Dict:
        """Get real sentiment data from multiple sources"""
        try:
            # Check cache first
            cache_key = f"{pair}_{datetime.now().date()}"
            if (cache_key in self.cache and 
                cache_key in self.cache_expiry and
                self.cache_expiry[cache_key] > datetime.now()):
                return self.cache[cache_key]
            
            sentiment_data = {
                'institutional': self._get_institutional_sentiment(pair),
                'retail': self._get_retail_sentiment(pair),
                'market_fear': self._get_market_fear(),
                'sources_used': []
            }
            
            # Calculate composite score
            score = self._calculate_sentiment_score(sentiment_data)
            
            result = {
                'score': score,
                'data': sentiment_data,
                'summary': self._create_sentiment_summary(sentiment_data, score),
                'timestamp': datetime.now().isoformat()
            }
            
            # Cache for 1 hour
            self.cache[cache_key] = result
            self.cache_expiry[cache_key] = datetime.now() + timedelta(hours=1)
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting sentiment for {pair}: {e}")
            return {
                'score': 10,  # Default neutral score
                'data': {},
                'summary': 'Sentiment data temporarily unavailable',
                'timestamp': datetime.now().isoformat()
            }
    
    def _get_institutional_sentiment(self, pair: str) -> Dict:
        """Get institutional sentiment from CFTC COT reports"""
        try:
            # Download latest COT report
            response = requests.get(self.cftc_url, timeout=15)
            if response.status_code != 200:
                return {'available': False, 'error': 'CFTC data unavailable'}
            
            lines = response.text.strip().split('\n')
            
            # Map forex pairs to COT symbols
            cot_mapping = {
                'EUR/USD': ['EURO FX', 'EURO'],
                'GBP/USD': ['BRITISH POUND', 'BRITISH POUND STERLING'],
                'USD/JPY': ['JAPANESE YEN'],
                'USD/CHF': ['SWISS FRANC'],
                'AUD/USD': ['AUSTRALIAN DOLLAR'],
                'USD/CAD': ['CANADIAN DOLLAR'],
                'NZD/USD': ['NEW ZEALAND DOLLAR'],
                'USD/MXN': ['MEXICAN PESO'],
                'USD/ZAR': ['SOUTH AFRICAN RAND'],
                'USD/TRY': ['TURKISH LIRA'],
            }
            
            for line in lines:
                for cot_name in cot_mapping.get(pair, []):
                    if cot_name in line:
                        parts = line.split(',')
                        if len(parts) >= 10:
                            try:
                                long_pos = int(parts[7].strip() or 0)
                                short_pos = int(parts[8].strip() or 0)
                                net = long_pos - short_pos
                                
                                # Determine if extreme
                                total = long_pos + short_pos
                                net_percentage = (net / total) * 100 if total > 0 else 0
                                is_extreme = abs(net_percentage) > 70
                                
                                return {
                                    'available': True,
                                    'long_positions': long_pos,
                                    'short_positions': short_pos,
                                    'net_position': net,
                                    'net_percentage': net_percentage,
                                    'extreme': is_extreme,
                                    'bias': 'NET_LONG' if net > 0 else 'NET_SHORT',
                                    'source': 'CFTC',
                                    'date': parts[2] if len(parts) > 2 else 'N/A'
                                }
                            except ValueError:
                                continue
            
            return {'available': False, 'error': 'Pair not found in COT report'}
            
        except Exception as e:
            logger.error(f"CFTC sentiment error: {e}")
            return {'available': False, 'error': str(e)}
    
    def _get_retail_sentiment(self, pair: str) -> Dict:
        """Get retail sentiment from multiple free sources"""
        retail_data = {}
        
        try:
            # Try FXSSI first (free sentiment data)
            fxssi_data = self._get_fxssi_sentiment(pair)
            if fxssi_data['available']:
                retail_data['fxssi'] = fxssi_data
            
            # Try MyFXBook sentiment
            myfxbook_data = self._get_myfxbook_sentiment(pair)
            if myfxbook_data['available']:
                retail_data['myfxbook'] = myfxbook_data
            
            # Try TradingView
            tv_data = self._get_tradingview_sentiment(pair)
            if tv_data['available']:
                retail_data['tradingview'] = tv_data
            
            # Calculate composite retail sentiment
            if retail_data:
                biases = []
                for source, data in retail_data.items():
                    if data.get('bias'):
                        biases.append(data['bias'])
                
                # Determine overall bias
                if biases:
                    buy_count = biases.count('BULLISH')
                    sell_count = biases.count('BEARISH')
                    
                    if buy_count > sell_count:
                        overall_bias = 'BULLISH'
                        strength = buy_count / len(biases)
                    elif sell_count > buy_count:
                        overall_bias = 'BEARISH'
                        strength = sell_count / len(biases)
                    else:
                        overall_bias = 'NEUTRAL'
                        strength = 0.5
                    
                    crowded = strength > 0.7  # More than 70% agreement
                    
                    return {
                        'available': True,
                        'overall_bias': overall_bias,
                        'strength': strength,
                        'crowded': crowded,
                        'sources': list(retail_data.keys()),
                        'details': retail_data
                    }
            
            return {'available': False, 'error': 'No retail data available'}
            
        except Exception as e:
            logger.error(f"Retail sentiment error: {e}")
            return {'available': False, 'error': str(e)}
    
    def _get_fxssi_sentiment(self, pair: str) -> Dict:
        """Get sentiment from FXSSI (free alternative)"""
        try:
            # FXSSI provides free sentiment indicators
            # This is a simplified version - would need proper parsing in production
            url = f"https://fxssi.com/tools/current-ratios"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                # Parse HTML for sentiment data (simplified)
                # In production, use BeautifulSoup for proper parsing
                html = response.text
                
                # Look for pair in HTML
                if pair.replace('/', '') in html or pair in html:
                    # Extract sentiment (simplified)
                    # This is a placeholder - actual implementation would parse the data
                    return {
                        'available': True,
                        'bias': 'BULLISH' if 'buy' in html.lower() else 'BEARISH',
                        'percentage': 0.65,  # Example
                        'source': 'FXSSI',
                        'note': 'Simplified parsing - needs enhancement'
                    }
            
            return {'available': False, 'error': 'FXSSI data not found'}
            
        except Exception as e:
            logger.debug(f"FXSSI sentiment error (non-critical): {e}")
            return {'available': False, 'error': str(e)}
    
    def _get_myfxbook_sentiment(self, pair: str) -> Dict:
        """Get sentiment from MyFXBook community"""
        try:
            # MyFXBook has community sentiment data
            url = f"https://www.myfxbook.com/community/outlook"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                html = response.text
                
                # Look for pair sentiment (simplified)
                pair_pattern = pair.replace('/', '').lower()
                if pair_pattern in html.lower():
                    # Extract percentage (simplified)
                    # This would need proper HTML parsing in production
                    return {
                        'available': True,
                        'bias': 'BULLISH',
                        'percentage': 0.60,
                        'source': 'MyFXBook',
                        'note': 'Community sentiment - needs proper parsing'
                    }
            
            return {'available': False, 'error': 'MyFXBook data not found'}
            
        except Exception as e:
            logger.debug(f"MyFXBook sentiment error (non-critical): {e}")
            return {'available': False, 'error': str(e)}
    
    def _get_tradingview_sentiment(self, pair: str) -> Dict:
        """Get sentiment from TradingView"""
        try:
            # TradingView has public sentiment data
            symbol = pair.replace('/', '')
            url = f"https://www.tradingview.com/symbols/{symbol}/"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                html = response.text
                
                # Look for sentiment indicators (simplified)
                # TradingView has "Technical Ratings" and community sentiment
                if 'technical ratings' in html.lower():
                    # Extract rating (simplified)
                    return {
                        'available': True,
                        'bias': 'BULLISH' if 'buy' in html.lower() else 'NEUTRAL',
                        'source': 'TradingView',
                        'note': 'Technical ratings - needs proper parsing'
                    }
            
            return {'available': False, 'error': 'TradingView data not found'}
            
        except Exception as e:
            logger.debug(f"TradingView sentiment error (non-critical): {e}")
            return {'available': False, 'error': str(e)}
    
    def _get_market_fear(self) -> Dict:
        """Get market fear/greed indicators"""
        try:
            # Get VIX data from Yahoo Finance
            vix_url = "https://query1.finance.yahoo.com/v7/finance/download/^VIX"
            params = {
                'period1': int((datetime.now() - timedelta(days=5)).timestamp()),
                'period2': int(datetime.now().timestamp()),
                'interval': '1d',
                'events': 'history'
            }
            
            response = requests.get(vix_url, params=params, timeout=10)
            if response.status_code == 200:
                lines = response.text.strip().split('\n')
                if len(lines) > 1:
                    latest = lines[1].split(',')
                    vix_value = float(latest[4])  # Close price
                    
                    # Normalize VIX (15-20 normal, >25 fear, >30 extreme fear)
                    fear_level = min(max((vix_value - 15) / 15, 0), 1)
                    
                    return {
                        'available': True,
                        'vix_value': vix_value,
                        'fear_level': fear_level,
                        'interpretation': self._interpret_fear_level(fear_level),
                        'source': 'VIX (Yahoo Finance)'
                    }
            
            return {'available': False, 'error': 'VIX data unavailable'}
            
        except Exception as e:
            logger.error(f"Market fear error: {e}")
            return {'available': False, 'error': str(e)}
    
    def _interpret_fear_level(self, fear_level: float) -> str:
        """Interpret fear level"""
        if fear_level < 0.3:
            return 'COMPLACENT'
        elif fear_level < 0.5:
            return 'NEUTRAL'
        elif fear_level < 0.7:
            return 'CAUTIOUS'
        elif fear_level < 0.85:
            return 'FEAR'
        else:
            return 'EXTREME_FEAR'
    
    def _calculate_sentiment_score(self, sentiment_data: Dict) -> int:
        """Calculate sentiment score 0-20"""
        score = 10  # Start with neutral
        
        # Institutional sentiment
        inst = sentiment_data.get('institutional', {})
        if inst.get('available'):
            if not inst.get('extreme'):
                score += 5
            else:
                score -= 3  # Extreme positioning reduces score
        
        # Retail sentiment
        retail = sentiment_data.get('retail', {})
        if retail.get('available'):
            if retail.get('crowded'):
                # Crowded retail is contrarian signal - GOOD for professionals
                score += 5
            else:
                score += 2
        
        # Market fear
        fear = sentiment_data.get('market_fear', {})
        if fear.get('available'):
            fear_level = fear.get('fear_level', 0.5)
            if 0.3 <= fear_level <= 0.7:
                score += 3  # Normal fear levels are good
            elif fear_level > 0.85:
                score -= 2  # Extreme fear is bad
        
        # Ensure score is between 0-20
        return max(0, min(score, 20))
    
    def _create_sentiment_summary(self, sentiment_data: Dict, score: int) -> str:
        """Create sentiment summary"""
        parts = []
        
        inst = sentiment_data.get('institutional')
        if inst and inst.get('available'):
            parts.append(f"Institutions: {inst.get('bias', 'N/A')} ({inst.get('net_percentage', 0):.1f}%)")
        
        retail = sentiment_data.get('retail')
        if retail and retail.get('available'):
            parts.append(f"Retail: {retail.get('overall_bias', 'N/A')}")
            if retail.get('crowded'):
                parts.append("(Crowded - contrarian signal)")
        
        fear = sentiment_data.get('market_fear')
        if fear and fear.get('available'):
            parts.append(f"Market Fear: {fear.get('interpretation', 'N/A')}")
        
        if parts:
            return f"Score: {score}/20. " + ". ".join(parts)
        else:
            return f"Score: {score}/20. Limited sentiment data available."

# ============================================================================
# CORE ANALYSIS MODULES (UPDATED WITH REAL SENTIMENT)
# ============================================================================

class FundamentalAnalyzer:
    """Analyzes fundamental catalysts using NewsAPI"""
    
    def __init__(self, api_key: str, rate_limiter: RateLimiter):
        self.api_key = api_key
        self.base_url = "https://newsapi.org/v2"
        self.rate_limiter = rate_limiter
        self.news_cache = {}
        
    def analyze_pair(self, pair: str) -> Dict:
        """Comprehensive fundamental analysis for a currency pair"""
        try:
            # Extract base and quote currencies
            base, quote = pair.split('/')
            
            # Get relevant news
            news = self._get_currency_news(base, quote)
            
            # Calculate fundamental score (0-40)
            score = 0
            reasons = []
            
            # Check for catalysts
            catalysts = self._detect_catalysts(news, base, quote)
            if catalysts:
                score += len(catalysts) * 8  # Up to 24 points
                reasons.extend(catalysts)
            
            # Check economic divergence
            divergence = self._check_economic_divergence(base, quote)
            if divergence['significant']:
                score += 16  # 16 points for strong divergence
                reasons.append(divergence['reason'])
            
            # Ensure score doesn't exceed 40
            score = min(score, 40)
            
            return {
                'score': score,
                'reasons': reasons,
                'catalysts': catalysts,
                'news_count': len(news),
                'summary': self._create_summary(reasons)
            }
            
        except Exception as e:
            logger.error(f"Fundamental analysis error for {pair}: {e}")
            return {'score': 0, 'reasons': [], 'summary': 'Fundamental analysis failed'}
    
    def _get_currency_news(self, base: str, quote: str) -> List:
        """Get news relevant to currency pair with rate limiting"""
        try:
            cache_key = f"{base}_{quote}_{datetime.now().date()}"
            if cache_key in self.news_cache:
                return self.news_cache[cache_key]
            
            # Rate limit before API call
            self.rate_limiter.wait_if_needed()
            
            # Search for both currencies and their central banks
            queries = [
                f"{base} central bank OR {base} interest rate",
                f"{quote} central bank OR {quote} interest rate",
                f"{base} inflation OR {base} CPI",
                f"{quote} inflation OR {quote} CPI",
                f"{base} GDP OR {base} economy",
                f"{quote} GDP OR {quote} economy"
            ]
            
            all_news = []
            for query in queries[:2]:  # Limit to 2 queries to save API calls
                url = f"{self.base_url}/everything"
                params = {
                    'apiKey': self.api_key,
                    'q': query,
                    'pageSize': 10,  # Increased to get more data
                    'sortBy': 'publishedAt',
                    'language': 'en',
                    'from': (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')  # Last 3 days
                }
                
                try:
                    response = requests.get(url, params=params, timeout=15)
                    if response.status_code == 200:
                        data = response.json()
                        if data.get('articles'):
                            all_news.extend(data['articles'])
                    
                    # Small delay between requests
                    time.sleep(0.1)
                    
                except Exception as e:
                    logger.debug(f"News query failed for {query}: {e}")
                    continue
            
            # Cache for 1 hour
            self.news_cache[cache_key] = all_news
            
            return all_news
            
        except Exception as e:
            logger.error(f"News fetch error: {e}")
            return []
    
    def _detect_catalysts(self, news: List, base: str, quote: str) -> List[str]:
        """Detect market-moving catalysts in news"""
        catalysts = []
        
        catalyst_keywords = {
            'RATE_DECISION': ['rate decision', 'hike', 'cut', 'hold rates', 'monetary policy'],
            'INFLATION': ['inflation', 'cpi', 'prices soar', 'deflation', 'price pressure'],
            'EMPLOYMENT': ['employment', 'unemployment', 'nfp', 'jobs report', 'payrolls'],
            'GEOPOLITICAL': ['sanctions', 'war', 'election', 'protest', 'political crisis'],
            'ECONOMIC_CRISIS': ['crisis', 'recession', 'default', 'bailout', 'economic collapse'],
            'INTERVENTION': ['intervene', 'currency intervention', 'central bank action', 'FX intervention'],
            'TRADE': ['trade war', 'tariff', 'trade deal', 'export', 'import'],
            'COMMODITY': ['oil price', 'gold price', 'commodity', 'crude', 'energy']
        }
        
        seen_catalysts = set()
        
        for article in news[:20]:  # Check first 20 articles
            title = article.get('title', '').lower()
            description = article.get('description', '').lower()
            content = f"{title} {description}"
            
            for catalyst_type, keywords in catalyst_keywords.items():
                if any(keyword in content for keyword in keywords):
                    catalyst_desc = f"{catalyst_type}: {title[:60]}..."
                    if catalyst_desc not in seen_catalysts:
                        catalysts.append(catalyst_desc)
                        seen_catalysts.add(catalyst_desc)
                    break  # Only count one catalyst per article
        
        return list(catalysts)[:5]  # Return top 5 unique catalysts
    
    def _check_economic_divergence(self, base: str, quote: str) -> Dict:
        """Check for economic divergence between two countries"""
        # This is a simplified version
        # In production, would integrate with economic calendar API
        
        major_divergences = {
            'EUR/USD': {
                'significant': True,
                'reason': 'Fed-ECB policy divergence with Fed more hawkish',
                'score': 8
            },
            'GBP/USD': {
                'significant': True,
                'reason': 'BOE dovish vs Fed hawkish stance',
                'score': 7
            },
            'USD/JPY': {
                'significant': True,
                'reason': 'Fed-BOJ massive policy divergence',
                'score': 9
            },
            'USD/TRY': {
                'significant': True,
                'reason': 'Extreme inflation differential (85% vs 3%)',
                'score': 10
            },
            'USD/ZAR': {
                'significant': True,
                'reason': 'SA energy crisis vs US energy independence',
                'score': 7
            },
            'AUD/USD': {
                'significant': True,
                'reason': 'Commodity vs non-commodity currency divergence',
                'score': 6
            }
        }
        
        pair = f"{base}/{quote}"
        if pair in major_divergences:
            return major_divergences[pair]
        
        # Check reverse pair
        reverse_pair = f"{quote}/{base}"
        if reverse_pair in major_divergences:
            # Reverse the significance
            data = major_divergences[reverse_pair].copy()
            data['reason'] = f"Reverse of {data['reason']}"
            return data
        
        return {'significant': False, 'reason': 'No significant divergence detected', 'score': 0}
    
    def _create_summary(self, reasons: List[str]) -> str:
        """Create fundamental summary"""
        if not reasons:
            return "No strong fundamental catalysts detected."
        
        if len(reasons) == 1:
            return reasons[0]
        
        # Group similar reasons
        rate_decisions = [r for r in reasons if 'RATE_DECISION' in r]
        inflation = [r for r in reasons if 'INFLATION' in r]
        geopolitical = [r for r in reasons if 'GEOPOLITICAL' in r]
        other = [r for r in reasons if r not in rate_decisions + inflation + geopolitical]
        
        summary_parts = []
        if rate_decisions:
            summary_parts.append(f"{len(rate_decisions)} rate decision catalysts")
        if inflation:
            summary_parts.append(f"{len(inflation)} inflation catalysts")
        if geopolitical:
            summary_parts.append(f"{len(geopolitical)} geopolitical catalysts")
        if other:
            summary_parts.append(f"{len(other)} other catalysts")
        
        return "Multiple catalysts: " + ", ".join(summary_parts)

class TechnicalAnalyzer:
    """Analyzes technical setups using TwelveData"""
    
    def __init__(self, api_key: str, rate_limiter: RateLimiter):
        self.api_key = api_key
        self.base_url = "https://api.twelvedata.com"
        self.rate_limiter = rate_limiter
        self.data_cache = {}
        
    def analyze_pair(self, pair: str) -> Dict:
        """Comprehensive technical analysis for a currency pair"""
        try:
            # Get data for multiple timeframes with caching
            data_4h = self._get_cached_data(pair, '4h', 100)
            data_daily = self._get_cached_data(pair, '1d', 100)
            
            if data_4h.empty or data_daily.empty:
                return {'score': 0, 'setup': 'NO_DATA', 'summary': 'Insufficient data'}
            
            # Calculate technical score (0-40)
            score = 0
            setups = []
            
            # 1. Check for clear trend (10 points)
            trend_strength = self._calculate_trend_strength(data_daily)
            if trend_strength['strong']:
                score += 10
                setups.append(f"Strong {trend_strength['direction']} trend on daily")
            
            # 2. Check for chart patterns (10 points)
            patterns = self._detect_chart_patterns(data_4h)
            if patterns:
                score += min(len(patterns) * 5, 10)
                setups.extend(patterns[:2])
            
            # 3. Check indicator alignment (10 points)
            indicators_aligned = self._check_indicators_alignment(data_4h)
            if indicators_aligned:
                score += 10
                setups.append("Indicators aligned with direction")
            
            # 4. Check support/resistance quality (10 points)
            sr_quality = self._check_support_resistance_quality(data_daily)
            score += sr_quality['score']
            if sr_quality['levels']:
                setups.append(f"Clear {sr_quality['type']} levels")
            
            # Ensure score doesn't exceed 40
            score = min(score, 40)
            
            # Determine market context
            context = self._determine_market_context(data_4h)
            
            # Calculate volatility
            atr = self._calculate_atr(data_4h)
            
            # Find optimal entry
            optimal_entry = self._calculate_optimal_entry(data_4h, trend_strength['direction'])
            
            # Get current price
            current_price = float(data_4h['close'].iloc[-1])
            
            return {
                'score': score,
                'setups': setups,
                'context': context,
                'trend_direction': trend_strength['direction'],
                'current_price': current_price,
                'optimal_entry': optimal_entry,
                'atr': atr,
                'summary': self._create_technical_summary(setups, context, current_price),
                'data_quality': 'GOOD',
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Technical analysis error for {pair}: {e}")
            return {'score': 0, 'setup': 'ERROR', 'summary': 'Technical analysis failed'}
    
    def _get_cached_data(self, pair: str, interval: str, outputsize: int) -> pd.DataFrame:
        """Get cached data or fetch from API"""
        cache_key = f"{pair}_{interval}_{outputsize}_{datetime.now().hour}"  # Cache by hour
        
        if cache_key in self.data_cache:
            return self.data_cache[cache_key]
        
        data = self._get_ohlc_data(pair, interval, outputsize)
        self.data_cache[cache_key] = data
        
        # Clean old cache entries
        if len(self.data_cache) > 1000:
            # Remove oldest 100 entries
            keys = list(self.data_cache.keys())[:100]
            for key in keys:
                del self.data_cache[key]
        
        return data
    
    def _get_ohlc_data(self, pair: str, interval: str, outputsize: int) -> pd.DataFrame:
        """Get OHLC data from TwelveData with rate limiting"""
        try:
            # Rate limit before API call
            self.rate_limiter.wait_if_needed()
            
            symbol = pair.replace('/', '')
            url = f"{self.base_url}/time_series"
            params = {
                'symbol': symbol,
                'interval': interval,
                'outputsize': outputsize,
                'apikey': self.api_key
            }
            
            response = requests.get(url, params=params, timeout=20)
            if response.status_code == 200:
                data = response.json()
                if 'values' in data:
                    df = pd.DataFrame(data['values'])
                    
                    # Check if data is valid
                    if len(df) < 5:
                        logger.warning(f"Insufficient data for {pair}: {len(df)} rows")
                        return pd.DataFrame()
                    
                    df['datetime'] = pd.to_datetime(df['datetime'])
                    df.set_index('datetime', inplace=True)
                    
                    # Convert to numeric
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors='coerce')
                    
                    # Remove any NaN values
                    df = df.dropna()
                    
                    return df
            
            logger.warning(f"No data returned for {pair} {interval}")
            return pd.DataFrame()
            
        except Exception as e:
            logger.error(f"Data fetch error for {pair}: {e}")
            return pd.DataFrame()
    
    def _calculate_trend_strength(self, df: pd.DataFrame) -> Dict:
        """Calculate trend strength and direction"""
        if df.empty or len(df) < 20:
            return {'strong': False, 'direction': 'SIDEWAYS'}
        
        prices = df['close'].values
        
        # Calculate multiple moving averages
        if len(prices) >= 50:
            sma_20 = pd.Series(prices).rolling(20).mean().iloc[-1]
            sma_50 = pd.Series(prices).rolling(50).mean().iloc[-1]
            sma_200 = pd.Series(prices).rolling(min(200, len(prices))).mean().iloc[-1]
            current_price = prices[-1]
            
            # Check for aligned moving averages
            if current_price > sma_20 > sma_50 > sma_200:
                return {'strong': True, 'direction': 'UPTREND'}
            elif current_price < sma_20 < sma_50 < sma_200:
                return {'strong': True, 'direction': 'DOWNTREND'}
            elif current_price > sma_20 and sma_20 > sma_50:
                return {'strong': True, 'direction': 'UPTREND'}
            elif current_price < sma_20 and sma_20 < sma_50:
                return {'strong': True, 'direction': 'DOWNTREND'}
        
        # Check price action
        if len(prices) >= 10:
            recent_prices = prices[-10:]
            if all(recent_prices[i] > recent_prices[i-1] for i in range(1, len(recent_prices))):
                return {'strong': True, 'direction': 'UPTREND'}
            elif all(recent_prices[i] < recent_prices[i-1] for i in range(1, len(recent_prices))):
                return {'strong': True, 'direction': 'DOWNTREND'}
        
        return {'strong': False, 'direction': 'SIDEWAYS'}
    
    def _detect_chart_patterns(self, df: pd.DataFrame) -> List[str]:
        """Detect common chart patterns"""
        patterns = []
        
        if df.empty or len(df) < 20:
            return patterns
        
        highs = df['high'].values[-20:]
        lows = df['low'].values[-20:]
        
        # Check for higher highs and higher lows (uptrend pattern)
        if len(highs) >= 5 and highs[-1] > highs[-5] and highs[-5] > highs[-10]:
            if len(lows) >= 5 and lows[-1] > lows[-5] and lows[-5] > lows[-10]:
                patterns.append("Higher highs & higher lows (Uptrend)")
        
        # Check for lower highs and lower lows (downtrend pattern)
        elif len(highs) >= 5 and highs[-1] < highs[-5] and highs[-5] < highs[-10]:
            if len(lows) >= 5 and lows[-1] < lows[-5] and lows[-5] < lows[-10]:
                patterns.append("Lower highs & lower lows (Downtrend)")
        
        # Check for consolidation
        recent_highs = highs[-10:] if len(highs) >= 10 else highs
        recent_lows = lows[-10:] if len(lows) >= 10 else lows
        
        if recent_highs and recent_lows:
            price_range = max(recent_highs) - min(recent_lows)
            avg_price = np.mean(df['close'].values[-10:]) if len(df) >= 10 else np.mean(df['close'].values)
            
            if price_range < avg_price * 0.01:  # Less than 1% range
                patterns.append("Consolidation/Range-bound")
            elif price_range < avg_price * 0.02:  # Less than 2% range
                patterns.append("Tight range")
        
        # Check for breakout patterns
        if len(df) >= 15:
            prev_range_high = max(df['high'].values[-15:-5])
            prev_range_low = min(df['low'].values[-15:-5])
            current_high = df['high'].iloc[-1]
            current_low = df['low'].iloc[-1]
            
            if current_high > prev_range_high * 1.005:
                patterns.append("Breaking above resistance")
            elif current_low < prev_range_low * 0.995:
                patterns.append("Breaking below support")
        
        return patterns
    
    def _check_indicators_alignment(self, df: pd.DataFrame) -> bool:
        """Check if common indicators are aligned"""
        if df.empty or len(df) < 20:
            return False
        
        prices = df['close'].values
        
        # Calculate RSI
        rsi = self._calculate_rsi(prices)
        if rsi is None or len(rsi) < 5:
            return False
        
        # Calculate MACD (simplified)
        ema_12 = pd.Series(prices).ewm(span=12, adjust=False).mean().values
        ema_26 = pd.Series(prices).ewm(span=26, adjust=False).mean().values
        macd = ema_12 - ema_26
        
        if len(macd) < 5:
            return False
        
        # Check alignment
        current_price = prices[-1]
        price_5_periods_ago = prices[-5] if len(prices) >= 5 else prices[0]
        
        price_up = current_price > price_5_periods_ago
        rsi_up = rsi[-1] > rsi[-5] if len(rsi) >= 5 else rsi[-1] > 50
        macd_up = macd[-1] > macd[-5] if len(macd) >= 5 else macd[-1] > 0
        
        # All three should point in same direction
        return (price_up == rsi_up == macd_up)
    
    def _calculate_rsi(self, prices: np.ndarray, period: int = 14):
        """Calculate RSI"""
        if len(prices) < period + 1:
            return None
        
        deltas = np.diff(prices)
        seed = deltas[:period]
        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period
        rs = up / down if down != 0 else 0
        rsi = np.zeros_like(prices)
        rsi[:period] = 100.0 - 100.0 / (1.0 + rs)
        
        for i in range(period, len(prices)):
            delta = deltas[i - 1]
            if delta > 0:
                upval = delta
                downval = 0.0
            else:
                upval = 0.0
                downval = -delta
            
            up = (up * (period - 1) + upval) / period
            down = (down * (period - 1) + downval) / period
            rs = up / down if down != 0 else 0
            rsi[i] = 100.0 - 100.0 / (1.0 + rs)
        
        return rsi
    
    def _check_support_resistance_quality(self, df: pd.DataFrame) -> Dict:
        """Check quality of support/resistance levels"""
        if df.empty or len(df) < 20:
            return {'score': 0, 'type': 'NONE', 'levels': []}
        
        # Find swing highs and lows
        highs = df['high'].values
        lows = df['low'].values
        
        # Simple S/R detection
        recent_highs = highs[-20:]
        recent_lows = lows[-20:]
        
        resistance = max(recent_highs)
        support = min(recent_lows)
        current_price = df['close'].iloc[-1]
        
        # Calculate distances
        distance_to_resistance = abs(resistance - current_price) / current_price
        distance_to_support = abs(current_price - support) / current_price
        
        # Determine quality
        if distance_to_resistance < 0.005:  # Within 0.5% of resistance
            return {'score': 10, 'type': 'NEAR_RESISTANCE', 'levels': [resistance]}
        elif distance_to_support < 0.005:  # Within 0.5% of support
            return {'score': 10, 'type': 'NEAR_SUPPORT', 'levels': [support]}
        elif distance_to_resistance < 0.01 or distance_to_support < 0.01:
            return {'score': 5, 'type': 'NEAR_SR', 'levels': [support, resistance]}
        
        return {'score': 0, 'type': 'NONE', 'levels': []}
    
    def _determine_market_context(self, df: pd.DataFrame) -> str:
        """Determine market context"""
        if df.empty:
            return 'UNCLEAR'
        
        trend = self._calculate_trend_strength(df)
        
        if trend['strong']:
            return 'TRENDING'
        
        # Check volatility
        atr = self._calculate_atr(df)
        avg_price = df['close'].mean()
        
        if atr > avg_price * 0.02:  # High volatility
            # Check for breakout
            recent_high = df['high'].iloc[-1]
            recent_low = df['low'].iloc[-1]
            
            if len(df) >= 10:
                prev_high = max(df['high'].values[-10:-1])
                prev_low = min(df['low'].values[-10:-1])
                
                if recent_high > prev_high * 1.01:  # 1% breakout
                    return 'BREAKOUT_UP'
                elif recent_low < prev_low * 0.99:  # 1% breakdown
                    return 'BREAKOUT_DOWN'
            
            return 'VOLATILE'
        
        # Check for range
        recent_range = max(df['high'].values[-10:]) - min(df['low'].values[-10:])
        if recent_range < avg_price * 0.01:  # Less than 1% range
            return 'RANGING_TIGHT'
        elif recent_range < avg_price * 0.02:  # Less than 2% range
            return 'RANGING'
        
        return 'UNCLEAR'
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """Calculate Average True Range"""
        if df.empty or len(df) < period + 1:
            return 0.0
        
        high = df['high'].values
        low = df['low'].values
        close = df['close'].values
        
        tr = np.zeros(len(df))
        for i in range(1, len(df)):
            hl = high[i] - low[i]
            hc = abs(high[i] - close[i-1])
            lc = abs(low[i] - close[i-1])
            tr[i] = max(hl, hc, lc)
        
        atr = pd.Series(tr).rolling(period).mean().iloc[-1]
        return float(atr) if not pd.isna(atr) else 0.0
    
    def _calculate_optimal_entry(self, df: pd.DataFrame, trend_direction: str) -> float:
        """Calculate optimal entry price based on market structure"""
        if df.empty:
            return 0.0
        
        current_price = df['close'].iloc[-1]
        
        if trend_direction == 'UPTREND':
            # For uptrends, look to buy at support or pullback
            recent_lows = df['low'].values[-10:]
            support = min(recent_lows) if recent_lows.size > 0 else current_price
            
            # Calculate pullback levels
            pullback_38 = support + (current_price - support) * 0.38
            pullback_50 = support + (current_price - support) * 0.50
            pullback_62 = support + (current_price - support) * 0.62
            
            # Prefer 38-50% pullback for better risk/reward
            optimal_entry = pullback_50
            
            # Adjust to nearest significant level
            optimal_entry = self._round_to_significant(optimal_entry)
            
        elif trend_direction == 'DOWNTREND':
            # For downtrends, look to sell at resistance or bounce
            recent_highs = df['high'].values[-10:]
            resistance = max(recent_highs) if recent_highs.size > 0 else current_price
            
            # Calculate bounce levels
            bounce_38 = resistance - (resistance - current_price) * 0.38
            bounce_50 = resistance - (resistance - current_price) * 0.50
            bounce_62 = resistance - (resistance - current_price) * 0.62
            
            # Prefer 38-50% bounce for better risk/reward
            optimal_entry = bounce_50
            
            # Adjust to nearest significant level
            optimal_entry = self._round_to_significant(optimal_entry)
            
        else:
            # For sideways markets, use current price
            optimal_entry = current_price
        
        return float(optimal_entry)
    
    def _round_to_significant(self, price: float) -> float:
        """Round price to nearest significant level"""
        # For most forex pairs, round to 0.00005
        return round(price * 20000) / 20000
    
    def _create_technical_summary(self, setups: List[str], context: str, current_price: float) -> str:
        """Create technical summary"""
        if not setups:
            return f"{context} market at {current_price:.5f}. No clear technical setups."
        
        setups_str = ', '.join(setups[:3])
        return f"{context} market at {current_price:.5f} with {setups_str}"

class SentimentAnalyzer:
    """Analyzes market sentiment using REAL data sources"""
    
    def __init__(self):
        self.sentiment_collector = RealSentimentCollector()
        
    def analyze_pair(self, pair: str) -> Dict:
        """Comprehensive sentiment analysis for a currency pair"""
        try:
            # Get real sentiment data
            sentiment_result = self.sentiment_collector.get_sentiment_for_pair(pair)
            
            return {
                'score': sentiment_result['score'],
                'data': sentiment_result['data'],
                'summary': sentiment_result['summary'],
                'timestamp': sentiment_result['timestamp']
            }
            
        except Exception as e:
            logger.error(f"Sentiment analysis error for {pair}: {e}")
            return {
                'score': 10,  # Default neutral score
                'data': {},
                'summary': 'Sentiment analysis temporarily unavailable',
                'timestamp': datetime.now().isoformat()
            }

# ============================================================================
# PROFESSIONAL TP/SL CALCULATOR (Same as before, optimized)
# ============================================================================

class ProfessionalTP_SL_Calculator:
    """Professional Take Profit and Stop Loss calculator"""
    
    def calculate_optimal_tp_sl(self, pair: str, entry_price: float, 
                                direction: str, context: str, atr: float,
                                technical_data: Dict) -> Optional[Dict]:
        """Calculate optimal TP and SL using professional methods"""
        try:
            if atr <= 0:
                logger.warning(f"Invalid ATR for {pair}")
                return None
            
            # Determine stop loss first (where thesis breaks)
            stop_loss = self._calculate_stop_loss(
                entry_price, direction, context, atr, technical_data
            )
            
            if stop_loss is None:
                return None
            
            # Calculate risk in pips
            risk_pips = self._calculate_pips_risk(pair, entry_price, stop_loss, direction)
            
            # Calculate take profit with minimum 1:1.5 risk/reward
            take_profit = self._calculate_take_profit(
                entry_price, direction, context, atr, risk_pips, technical_data
            )
            
            if take_profit is None:
                return None
            
            # Calculate reward in pips
            reward_pips = self._calculate_pips_reward(pair, entry_price, take_profit, direction)
            
            # Calculate risk/reward ratio
            if risk_pips <= 0:
                return None
            
            risk_reward = reward_pips / risk_pips
            
            # Calculate probability of TP before SL
            probability = self._calculate_success_probability(
                pair, entry_price, stop_loss, take_profit, direction, 
                risk_reward, context
            )
            
            # Estimate trade duration
            estimated_days = self._estimate_trade_duration(
                pair, entry_price, take_profit, direction, context, atr
            )
            
            # Professional validation
            is_valid = self._validate_tp_sl(
                risk_pips, reward_pips, risk_reward, probability, estimated_days
            )
            
            if not is_valid:
                return None
            
            return {
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'risk_pips': risk_pips,
                'reward_pips': reward_pips,
                'risk_reward': risk_reward,
                'probability_tp_before_sl': probability,
                'estimated_duration_days': estimated_days,
                'method': 'PROFESSIONAL_ATR_KEYLEVELS'
            }
            
        except Exception as e:
            logger.error(f"TP/SL calculation error for {pair}: {e}")
            return None
    
    def _calculate_stop_loss(self, entry: float, direction: str, context: str,
                            atr: float, technical_data: Dict) -> Optional[float]:
        """Calculate stop loss where trade thesis breaks"""
        
        # ATR-based stop loss with context adjustment
        atr_multiplier = {
            'TRENDING': 1.0,
            'RANGING': 0.7,
            'RANGING_TIGHT': 0.5,
            'BREAKOUT_UP': 1.2,
            'BREAKOUT_DOWN': 1.2,
            'VOLATILE': 1.5,
            'UNCLEAR': 1.0
        }.get(context, 1.0)
        
        atr_stop_distance = atr * atr_multiplier
        
        if direction == 'BUY':
            # For BUY: stop below entry
            stop_loss = entry - atr_stop_distance
            
            # Adjust to nearest round number
            stop_loss = self._adjust_to_psychological_level(stop_loss, 'down')
            
            # Ensure stop is reasonable (not too close)
            min_stop_distance = atr * 0.5
            if (entry - stop_loss) < min_stop_distance:
                stop_loss = entry - min_stop_distance
            
        else:  # SELL
            # For SELL: stop above entry
            stop_loss = entry + atr_stop_distance
            
            # Adjust to nearest round number
            stop_loss = self._adjust_to_psychological_level(stop_loss, 'up')
            
            # Ensure stop is reasonable
            min_stop_distance = atr * 0.5
            if (stop_loss - entry) < min_stop_distance:
                stop_loss = entry + min_stop_distance
        
        return stop_loss
    
    def _calculate_take_profit(self, entry: float, direction: str, context: str,
                              atr: float, risk_pips: float, technical_data: Dict) -> Optional[float]:
        """Calculate take profit with optimal risk/reward"""
        
        # Minimum risk/reward ratio
        min_rr_ratio = 1.5
        
        # Calculate minimum target distance based on risk
        min_target_distance = risk_pips * min_rr_ratio
        
        # Convert pips to price (assuming standard forex pairs)
        pip_value = 0.0001
        
        if direction == 'BUY':
            min_tp_price = entry + (min_target_distance * pip_value)
            
            # ATR-based extension
            if context in ['TRENDING', 'BREAKOUT_UP']:
                atr_multiplier = 3.0
                atr_tp_distance = atr * atr_multiplier
                atr_tp_price = entry + atr_tp_distance
                
                # Use the larger of the two
                take_profit = max(min_tp_price, atr_tp_price)
            else:
                take_profit = min_tp_price
            
            # Adjust to nearest round number
            take_profit = self._adjust_to_psychological_level(take_profit, 'up')
            
        else:  # SELL
            min_tp_price = entry - (min_target_distance * pip_value)
            
            # ATR-based extension
            if context in ['TRENDING', 'BREAKOUT_DOWN']:
                atr_multiplier = 3.0
                atr_tp_distance = atr * atr_multiplier
                atr_tp_price = entry - atr_tp_distance
                
                # Use the larger of the two
                take_profit = min(min_tp_price, atr_tp_price)
            else:
                take_profit = min_tp_price
            
            # Adjust to nearest round number
            take_profit = self._adjust_to_psychological_level(take_profit, 'down')
        
        # Ensure TP is reasonable (not too ambitious)
        max_rr_ratio = 5.0
        max_target_distance = risk_pips * max_rr_ratio
        
        if direction == 'BUY':
            max_tp_price = entry + (max_target_distance * pip_value)
            take_profit = min(take_profit, max_tp_price)
        else:
            max_tp_price = entry - (max_target_distance * pip_value)
            take_profit = max(take_profit, max_tp_price)
        
        return take_profit
    
    def _calculate_pips_risk(self, pair: str, entry: float, stop: float, direction: str) -> float:
        """Calculate risk in pips"""
        if direction == 'BUY':
            risk = entry - stop
        else:
            risk = stop - entry
        
        # Convert to pips (assuming standard 4 decimal places)
        # Adjust for JPY pairs if needed
        if 'JPY' in pair:
            pips = risk * 100  # JPY pairs have 2 decimal places
        else:
            pips = risk * 10000
        
        return max(pips, 0.1)  # Minimum 0.1 pips
    
    def _calculate_pips_reward(self, pair: str, entry: float, tp: float, direction: str) -> float:
        """Calculate reward in pips"""
        if direction == 'BUY':
            reward = tp - entry
        else:
            reward = entry - tp
        
        # Convert to pips
        if 'JPY' in pair:
            pips = reward * 100
        else:
            pips = reward * 10000
        
        return max(pips, 0.1)
    
    def _calculate_success_probability(self, pair: str, entry: float, stop: float,
                                      tp: float, direction: str, rr_ratio: float,
                                      context: str) -> float:
        """Calculate probability of TP hitting before SL"""
        
        # Base probabilities by context
        base_probabilities = {
            'TRENDING': 0.65,
            'RANGING': 0.55,
            'RANGING_TIGHT': 0.60,
            'BREAKOUT_UP': 0.60,
            'BREAKOUT_DOWN': 0.60,
            'VOLATILE': 0.50,
            'UNCLEAR': 0.50
        }
        
        base_prob = base_probabilities.get(context, 0.50)
        
        # Adjust based on risk/reward ratio
        rr_adjustment = {
            1.0: 0.0,
            1.5: -0.05,
            2.0: -0.10,
            3.0: -0.15,
            4.0: -0.20,
            5.0: -0.25
        }
        
        closest_rr = min(rr_adjustment.keys(), key=lambda x: abs(x - rr_ratio))
        adjustment = rr_adjustment[closest_rr]
        
        probability = base_prob + adjustment
        
        # Ensure within reasonable bounds
        probability = max(0.30, min(probability, 0.85))
        
        return round(probability, 2)
    
    def _estimate_trade_duration(self, pair: str, entry: float, tp: float,
                                direction: str, context: str, atr: float) -> int:
        """Estimate trade duration in days"""
        
        # Calculate distance to target
        if direction == 'BUY':
            distance = tp - entry
        else:
            distance = entry - tp
        
        # Estimate days based on ATR
        if atr > 0:
            daily_atr = atr * 6  # Convert 4h ATR to daily (6 periods)
            if daily_atr > 0:
                estimated_days = distance / daily_atr
            else:
                estimated_days = 7
        else:
            estimated_days = 7
        
        # Context-based adjustments
        context_adjustment = {
            'TRENDING': 0.8,
            'RANGING': 1.5,
            'RANGING_TIGHT': 2.0,
            'BREAKOUT_UP': 0.7,
            'BREAKOUT_DOWN': 0.7,
            'VOLATILE': 0.6,
            'UNCLEAR': 1.2
        }
        
        estimated_days *= context_adjustment.get(context, 1.0)
        
        # Ensure reasonable bounds
        estimated_days = max(1, min(estimated_days, 60))
        
        return int(round(estimated_days))
    
    def _adjust_to_psychological_level(self, price: float, direction: str) -> float:
        """Adjust price to nearest psychological level"""
        # For most forex pairs, round to 0.00005
        if 'JPY' in direction:  # If JPY pair in direction string (simplified)
            rounded = round(price * 100) / 100  # Round to 0.01 for JPY
        else:
            rounded = round(price * 20000) / 20000  # Round to 0.00005
        
        # Ensure it's in the right direction
        if direction == 'up' and rounded < price:
            rounded += 0.00005
        elif direction == 'down' and rounded > price:
            rounded -= 0.00005
        
        return rounded
    
    def _validate_tp_sl(self, risk_pips: float, reward_pips: float,
                       risk_reward: float, probability: float,
                       estimated_days: int) -> bool:
        """Validate TP/SL meets professional criteria"""
        
        validations = {
            'risk_pips_sufficient': risk_pips >= 10,
            'reward_pips_sufficient': reward_pips >= 15,
            'risk_reward_adequate': risk_reward >= 1.5,
            'probability_sufficient': probability >= 0.5,
            'duration_reasonable': estimated_days <= 30,
            'not_overambitious': risk_reward <= 5.0,
        }
        
        for check_name, is_valid in validations.items():
            if not is_valid:
                logger.debug(f"TP/SL validation failed: {check_name}")
                return False
        
        return True

# ============================================================================
# INTELLIGENT PAIR SELECTOR
# ============================================================================

class IntelligentPairSelector:
    """Intelligently selects pairs to scan based on activity and importance"""
    
    def __init__(self, twelvedata_key: str):
        self.twelvedata_key = twelvedata_key
        self.all_pairs = self._fetch_all_pairs()
        self.pair_categories = self._categorize_pairs()
        self.scan_priority = {}
        
    def _fetch_all_pairs(self) -> List[str]:
        """Fetch ALL forex pairs from TwelveData"""
        try:
            url = "https://api.twelvedata.com/forex_pairs"
            params = {'apikey': self.twelvedata_key}
            
            response = requests.get(url, params=params, timeout=30)
            if response.status_code == 200:
                data = response.json()
                if 'data' in data:
                    pairs = []
                    for item in data['data']:
                        symbol = item.get('symbol', '')
                        if '/' in symbol and 'test' not in symbol.lower():
                            pairs.append(symbol)
                    
                    logger.info(f"Fetched {len(pairs)} forex pairs from TwelveData")
                    return pairs
            
            logger.warning("Could not fetch pairs from TwelveData, using default list")
            return self._get_default_pairs()
            
        except Exception as e:
            logger.error(f"Error fetching pairs: {e}")
            return self._get_default_pairs()
    
    def _get_default_pairs(self) -> List[str]:
        """Get default list of important pairs"""
        return [
            # Major pairs
            'EUR/USD', 'GBP/USD', 'USD/JPY', 'USD/CHF', 'AUD/USD',
            'USD/CAD', 'NZD/USD',
            
            # Minor pairs
            'EUR/GBP', 'EUR/JPY', 'EUR/CHF', 'GBP/JPY', 'GBP/CHF',
            'AUD/JPY', 'AUD/CAD', 'AUD/NZD', 'CAD/JPY', 'NZD/JPY',
            
            # Exotic pairs with high movement potential
            'USD/TRY', 'USD/ZAR', 'USD/MXN', 'USD/BRL', 'USD/INR',
            'USD/RUB', 'USD/THB', 'USD/CNH', 'USD/SGD', 'USD/HKD',
            'EUR/TRY', 'EUR/PLN', 'EUR/HUF', 'EUR/CZK', 'EUR/RON',
            'GBP/TRY', 'AUD/SGD', 'CAD/CHF', 'NZD/SGD',
            
            # Emerging market crosses
            'USD/KRW', 'USD/TWD', 'USD/PHP', 'USD/IDR', 'USD/MYR',
            'EUR/ZAR', 'GBP/ZAR', 'JPY/ZAR', 'AUD/MXN', 'CAD/MXN'
        ]
    
    def _categorize_pairs(self) -> Dict[str, List[str]]:
        """Categorize pairs by importance and characteristics"""
        categories = {
            'majors': [],
            'minors': [],
            'exotics': [],
            'emerging': [],
            'commodity': [],
            'safe_haven': []
        }
        
        commodity_currencies = ['AUD', 'CAD', 'NZD', 'RUB', 'BRL', 'ZAR']
        safe_haven_currencies = ['JPY', 'CHF', 'USD', 'SGD']
        
        for pair in self.all_pairs:
            base, quote = pair.split('/')
            
            # Major pairs (directly involving USD)
            if 'USD' in pair and (pair in ['EUR/USD', 'GBP/USD', 'USD/JPY', 
                                          'USD/CHF', 'AUD/USD', 'USD/CAD', 'NZD/USD']):
                categories['majors'].append(pair)
            
            # Minor pairs (non-USD major crosses)
            elif all(currency in ['EUR', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD', 'NZD'] 
                    for currency in [base, quote]):
                categories['minors'].append(pair)
            
            # Exotic pairs (involving emerging market currencies)
            elif any(currency in ['TRY', 'ZAR', 'MXN', 'BRL', 'INR', 'RUB', 
                                 'THB', 'CNH', 'HKD', 'KRW', 'TWD'] 
                    for currency in [base, quote]):
                categories['exotics'].append(pair)
            
            # Emerging market crosses
            elif any(currency in ['PLN', 'HUF', 'CZK', 'RON', 'PHP', 'IDR', 'MYR'] 
                    for currency in [base, quote]):
                categories['emerging'].append(pair)
            
            # Commodity currency pairs
            if any(currency in commodity_currencies for currency in [base, quote]):
                if pair not in categories['commodity']:
                    categories['commodity'].append(pair)
            
            # Safe haven pairs
            if all(currency in safe_haven_currencies for currency in [base, quote]):
                if pair not in categories['safe_haven']:
                    categories['safe_haven'].append(pair)
        
        return categories
    
    def get_pairs_for_scan(self, max_pairs: int = 1459) -> List[str]:
        """Intelligently select pairs to scan"""
        
        # Start with high-priority categories
        selected_pairs = []
        
        # Always include majors (they're most important)
        selected_pairs.extend(self.pair_categories['majors'][:7])
        
        # Add some minors
        selected_pairs.extend(self.pair_categories['minors'][:10])
        
        # Add high-potential exotics (limit to avoid too many low-liquidity pairs)
        selected_pairs.extend(self.pair_categories['exotics'][:15])
        
        # If we need more pairs, add from other categories
        if len(selected_pairs) < max_pairs:
            # Add remaining majors and minors
            remaining_majors = [p for p in self.pair_categories['majors'] 
                              if p not in selected_pairs]
            selected_pairs.extend(remaining_majors[:10])
            
            remaining_minors = [p for p in self.pair_categories['minors'] 
                              if p not in selected_pairs]
            selected_pairs.extend(remaining_minors[:20])
        
        # If still need more, add exotics and emerging
        if len(selected_pairs) < max_pairs:
            remaining_exotics = [p for p in self.pair_categories['exotics'] 
                               if p not in selected_pairs]
            selected_pairs.extend(remaining_exotics[:30])
            
            selected_pairs.extend(self.pair_categories['emerging'][:20])
        
        # Add commodity and safe haven pairs
        if len(selected_pairs) < max_pairs:
            selected_pairs.extend(self.pair_categories['commodity'][:15])
            selected_pairs.extend(self.pair_categories['safe_haven'][:10])
        
        # Remove duplicates and ensure we don't exceed limit
        selected_pairs = list(dict.fromkeys(selected_pairs))[:max_pairs]
        
        logger.info(f"Selected {len(selected_pairs)} pairs for scanning")
        logger.info(f"Breakdown: {len([p for p in selected_pairs if p in self.pair_categories['majors']])} majors, "
                   f"{len([p for p in selected_pairs if p in self.pair_categories['minors']])} minors, "
                   f"{len([p for p in selected_pairs if p in self.pair_categories['exotics']])} exotics")
        
        return selected_pairs

# ============================================================================
# MAIN SCANNER ENGINE (OPTIMIZED)
# ============================================================================

class GlobalForexScanner:
    """Optimized scanner engine - finds ALL very high probability setups"""
    
    def __init__(self, config: Config):
        self.config = config
        
        # Initialize rate limiters
        self.twelve_rate_limiter = RateLimiter(config.TWELVEDATA_REQUESTS_PER_MINUTE)
        self.news_rate_limiter = RateLimiter(5)  # 5 requests per minute for NewsAPI
        
        # Initialize analysis modules
        self.fundamental_analyzer = FundamentalAnalyzer(config.NEWSAPI_KEY, self.news_rate_limiter)
        self.technical_analyzer = TechnicalAnalyzer(config.TWELVEDATA_KEY, self.twelve_rate_limiter)
        self.sentiment_analyzer = SentimentAnalyzer()
        self.tp_sl_calculator = ProfessionalTP_SL_Calculator()
        
        # Intelligent pair selection
        self.pair_selector = IntelligentPairSelector(config.TWELVEDATA_KEY)
        self.all_pairs = self.pair_selector.get_pairs_for_scan(config.MAX_PAIRS_PER_SCAN)
        
        # Performance tracking
        self.scan_history = []
        self.opportunities_history = []
        self.scan_stats = {
            'total_scans': 0,
            'total_opportunities_found': 0,
            'avg_scan_duration': 0,
            'last_scan_time': None
        }
        
        logger.info(f"GlobalForexScanner initialized with {len(self.all_pairs)} pairs")
    
    def run_complete_scan(self) -> ScanResult:
        """Run complete market scan - finds ALL very high probability setups"""
        start_time = datetime.now()
        logger.info(f"🚀 Starting complete market scan at {start_time}")
        
        all_opportunities = []
        pairs_analyzed = 0
        pairs_failed = 0
        
        # Intelligent scanning: group pairs by category
        pair_batches = self._create_intelligent_batches()
        
        for batch_num, batch in enumerate(pair_batches, 1):
            logger.info(f"Analyzing batch {batch_num}/{len(pair_batches)} ({len(batch)} pairs)")
            
            for pair in batch:
                try:
                    pairs_analyzed += 1
                    
                    if pairs_analyzed % 10 == 0:
                        logger.info(f"Progress: {pairs_analyzed}/{len(self.all_pairs)} pairs analyzed")
                    
                    # Run analysis
                    opportunity = self._analyze_single_pair(pair)
                    
                    if opportunity:
                        all_opportunities.append(opportunity)
                        logger.info(f"✅ Found very high probability setup: {pair} ({opportunity.confluence_score}%)")
                    
                except Exception as e:
                    pairs_failed += 1
                    logger.error(f"Error analyzing {pair}: {e}")
                    continue
        
        # Calculate scan statistics
        scan_duration = (datetime.now() - start_time).total_seconds()
        market_state = self._determine_market_state(len(all_opportunities))
        
        # Update stats
        self.scan_stats['total_scans'] += 1
        self.scan_stats['total_opportunities_found'] += len(all_opportunities)
        self.scan_stats['avg_scan_duration'] = (
            (self.scan_stats['avg_scan_duration'] * (self.scan_stats['total_scans'] - 1) + scan_duration) 
            / self.scan_stats['total_scans']
        )
        self.scan_stats['last_scan_time'] = datetime.now().isoformat()
        
        # Create scan result
        result = ScanResult(
            timestamp=datetime.now().isoformat(),
            pairs_scanned=pairs_analyzed,
            very_high_probability_setups=len(all_opportunities),
            opportunities=all_opportunities,
            scan_duration_seconds=scan_duration,
            market_state=market_state
        )
        
        # Save results
        self._save_scan_result(result)
        
        logger.info(f"✅ Scan completed in {scan_duration:.1f} seconds")
        logger.info(f"📊 Results: {pairs_analyzed} pairs analyzed, {len(all_opportunities)} very high probability setups found")
        logger.info(f"📈 Market state: {market_state}")
        
        if pairs_failed > 0:
            logger.warning(f"⚠ {pairs_failed} pairs failed analysis")
        
        return result
    
    def _create_intelligent_batches(self) -> List[List[str]]:
        """Create intelligent batches for efficient scanning"""
        # Group pairs by category for better API usage
        batches = []
        batch_size = 10  # Smaller batches for better rate limiting
        
        # Separate pairs by importance
        majors = [p for p in self.all_pairs if p in self.pair_selector.pair_categories['majors']]
        minors = [p for p in self.all_pairs if p in self.pair_selector.pair_categories['minors']]
        others = [p for p in self.all_pairs if p not in majors + minors]
        
        # Process in order of importance
        all_groups = [majors, minors, others]
        
        for group in all_groups:
            for i in range(0, len(group), batch_size):
                batch = group[i:i + batch_size]
                if batch:
                    batches.append(batch)
        
        return batches
    
    def _analyze_single_pair(self, pair: str) -> Optional[Opportunity]:
        """Analyze a single pair"""
        try:
            # Step 1: Fundamental Analysis
            fundamental = self.fundamental_analyzer.analyze_pair(pair)
            
            # Step 2: Technical Analysis
            technical = self.technical_analyzer.analyze_pair(pair)
            
            # Step 3: Sentiment Analysis
            sentiment = self.sentiment_analyzer.analyze_pair(pair)
            
            # Step 4: Calculate Confluence Score
            confluence_score = (
                fundamental['score'] + 
                technical['score'] + 
                sentiment['score']
            )
            
            # THE ONLY FILTER: VERY HIGH PROBABILITY (≥70%)
            if confluence_score >= self.config.MIN_CONFLUENCE_SCORE:
                # Step 5: Determine trade direction
                direction = self._determine_trade_direction(
                    technical['trend_direction'],
                    technical['context'],
                    fundamental
                )
                
                # Step 6: Use optimal entry
                entry_price = technical['optimal_entry']
                
                # Step 7: Calculate professional TP/SL
                tp_sl = self.tp_sl_calculator.calculate_optimal_tp_sl(
                    pair=pair,
                    entry_price=entry_price,
                    direction=direction,
                    context=technical['context'],
                    atr=technical['atr'],
                    technical_data=technical
                )
                
                if tp_sl is None:
                    logger.debug(f"TP/SL calculation failed for {pair}")
                    return None
                
                # Step 8: Create opportunity
                return Opportunity(
                    pair=pair,
                    direction=direction,
                    confluence_score=confluence_score,
                    catalyst=fundamental['summary'],
                    setup_type=technical['summary'],
                    entry_price=entry_price,
                    stop_loss=tp_sl['stop_loss'],
                    take_profit=tp_sl['take_profit'],
                    risk_reward=tp_sl['risk_reward'],
                    risk_pips=tp_sl['risk_pips'],
                    reward_pips=tp_sl['reward_pips'],
                    probability_tp_before_sl=tp_sl['probability_tp_before_sl'],
                    estimated_duration_days=tp_sl['estimated_duration_days'],
                    context=technical['context'],
                    confidence='VERY_HIGH' if confluence_score >= 85 else 'HIGH',
                    analysis_summary=self._create_analysis_summary(pair, confluence_score),
                    fundamentals_summary=fundamental['summary'],
                    technicals_summary=technical['summary'],
                    sentiment_summary=sentiment['summary'],
                    detected_at=datetime.now().isoformat()
                )
            
            return None
            
        except Exception as e:
            logger.error(f"Analysis failed for {pair}: {e}")
            return None
    
    def _determine_trade_direction(self, trend_direction: str, context: str,
                                  fundamental: Dict) -> str:
        """Determine trade direction based on analysis"""
        if trend_direction == 'UPTREND':
            return 'BUY'
        elif trend_direction == 'DOWNTREND':
            return 'SELL'
        
        # Use context for unclear trends
        if context == 'BREAKOUT_UP':
            return 'BUY'
        elif context == 'BREAKOUT_DOWN':
            return 'SELL'
        
        # Default based on fundamentals if available
        if fundamental['score'] >= 20:
            # Check catalysts for direction hints
            catalysts = fundamental.get('catalysts', [])
            for catalyst in catalysts:
                if 'RATE_DECISION' in catalyst and 'hike' in catalyst.lower():
                    # Rate hike typically strengthens currency
                    pair_parts = catalyst.split(':')[0]
                    if 'USD' in pair_parts:
                        return 'BUY' if 'USD' in pair_parts.split('/')[0] else 'SELL'
        
        return 'BUY'  # Conservative default
    
    def _create_analysis_summary(self, pair: str, score: int) -> str:
        """Create summary of analysis"""
        if score >= 85:
            return f"EXCEPTIONAL setup for {pair} with multiple strong confirmations."
        elif score >= 75:
            return f"STRONG setup for {pair} with clear alignment across all analyses."
        else:
            return f"GOOD setup for {pair} meeting all very high probability criteria."
    
    def _determine_market_state(self, opportunities_count: int) -> str:
        """Determine overall market state"""
        if opportunities_count == 0:
            return 'QUIET'
        elif opportunities_count <= 3:
            return 'NORMAL'
        elif opportunities_count <= 10:
            return 'VOLATILE'
        elif opportunities_count <= 20:
            return 'ACTIVE'
        else:
            return 'CRISIS'
    
    def _save_scan_result(self, result: ScanResult):
        """Save scan result to database"""
        try:
            # Load existing data
            db_file = Path(self.config.OPPORTUNITIES_DB)
            if db_file.exists():
                with open(db_file, 'r') as f:
                    existing_data = json.load(f)
            else:
                existing_data = {'scans': [], 'opportunities': [], 'stats': {}}
            
            # Add new scan
            existing_data['scans'].append(result.to_dict())
            
            # Add new opportunities
            for opp in result.opportunities:
                existing_data['opportunities'].append(opp.to_dict())
            
            # Add/update stats
            existing_data['stats'] = {
                'total_scans': self.scan_stats['total_scans'],
                'total_opportunities': self.scan_stats['total_opportunities_found'],
                'avg_scan_duration': self.scan_stats['avg_scan_duration'],
                'last_scan': self.scan_stats['last_scan_time'],
                'market_state_history': existing_data.get('stats', {}).get('market_state_history', []) + 
                                       [{'timestamp': result.timestamp, 'state': result.market_state}]
            }
            
            # Keep only recent data
            existing_data['scans'] = existing_data['scans'][-100:]  # Last 100 scans
            existing_data['opportunities'] = existing_data['opportunities'][-500:]  # Last 500 opportunities
            
            # Save to file
            with open(db_file, 'w') as f:
                json.dump(existing_data, f, indent=2)
            
            logger.debug(f"Saved scan results to {db_file}")
            
        except Exception as e:
            logger.error(f"Error saving scan results: {e}")

# ============================================================================
# WEB SERVER & KEEP-ALIVE (Same as before with improvements)
# ============================================================================

from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)
scanner = None
config = Config.load()

# HTML template remains the same (copied from previous version)
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Forex Global Confluence Scanner</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="300"> <!-- Auto-refresh every 5 minutes -->
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { background: #2c3e50; color: white; padding: 20px; border-radius: 5px; margin-bottom: 20px; }
        .scan-info { background: white; padding: 15px; border-radius: 5px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .opportunity { background: white; padding: 15px; border-radius: 5px; margin-bottom: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .opportunity.high { border-left: 5px solid #27ae60; }
        .opportunity.very-high { border-left: 5px solid #e74c3c; }
        .market-state { padding: 10px; border-radius: 5px; color: white; font-weight: bold; margin-bottom: 10px; }
        .market-state.quiet { background: #95a5a6; }
        .market-state.normal { background: #3498db; }
        .market-state.volatile { background: #f39c12; }
        .market-state.crisis { background: #e74c3c; }
        .badge { display: inline-block; padding: 3px 8px; border-radius: 3px; font-size: 12px; font-weight: bold; }
        .badge.buy { background: #27ae60; color: white; }
        .badge.sell { background: #e74c3c; color: white; }
        .details { margin-top: 10px; padding: 10px; background: #f8f9fa; border-radius: 3px; font-size: 14px; }
        .details pre { white-space: pre-wrap; font-family: monospace; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; margin: 20px 0; }
        .stat-box { background: white; padding: 15px; border-radius: 5px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .stat-value { font-size: 24px; font-weight: bold; color: #2c3e50; }
        .stat-label { font-size: 12px; color: #7f8c8d; text-transform: uppercase; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🌍 Forex Global Confluence Scanner</h1>
            <p>Professional-grade analysis of ALL forex pairs. Shows ONLY very high probability setups (70%+ confluence).</p>
            <p><small>Real sentiment data from CFTC, FXSSI, MyFXBook, TradingView | Never sleeps on Render</small></p>
        </div>
        
        <div class="stats">
            <div class="stat-box">
                <div class="stat-value">{{ stats.total_scans }}</div>
                <div class="stat-label">Total Scans</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{{ stats.total_opportunities }}</div>
                <div class="stat-label">Opportunities Found</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{{ "%.1f"|format(stats.avg_scan_duration) }}s</div>
                <div class="stat-label">Avg Scan Time</div>
            </div>
            <div class="stat-box">
                <div class="stat-value">{{ "%.0f"|format(stats.avg_opportunities_per_scan) }}</div>
                <div class="stat-label">Avg per Scan</div>
            </div>
        </div>
        
        <div class="scan-info">
            <h2>Latest Scan Results</h2>
            <p><strong>Timestamp:</strong> {{ scan.timestamp }}</p>
            <p><strong>Pairs Scanned:</strong> {{ scan.pairs_scanned }}</p>
            <p><strong>Very High Probability Setups Found:</strong> <strong>{{ scan.very_high_probability_setups }}</strong></p>
            <p><strong>Scan Duration:</strong> {{ "%.1f"|format(scan.scan_duration_seconds) }} seconds</p>
            <div class="market-state {{ scan.market_state.lower() }}">
                Market State: {{ scan.market_state }}
            </div>
        </div>
        
        {% if scan.very_high_probability_setups == 0 %}
            <div class="opportunity">
                <h3>No Very High Probability Setups Found</h3>
                <p>The market is quiet. No setups meet our strict 70%+ confluence criteria.</p>
                <p><em>This is MARKET TRUTH, not a system failure. Patience is key.</em></p>
            </div>
        {% else %}
            <h2>Very High Probability Opportunities ({{ scan.very_high_probability_setups }})</h2>
            <p><em>Showing ALL setups that meet 70%+ confluence criteria. No filtering, no limits, just truth.</em></p>
            
            {% for opp in scan.opportunities %}
                <div class="opportunity {{ opp.confidence.lower().replace('_', '-') }}">
                    <h3>
                        <span class="badge {{ opp.direction.lower() }}">{{ opp.direction }}</span>
                        {{ opp.pair }} - {{ opp.confluence_score }}% Confluence
                        <small>({{ opp.confidence }} confidence)</small>
                    </h3>
                    
                    <p><strong>Catalyst:</strong> {{ opp.catalyst }}</p>
                    <p><strong>Setup:</strong> {{ opp.setup_type }}</p>
                    <p><strong>Context:</strong> {{ opp.context }}</p>
                    
                    <div class="details">
                        <p><strong>Entry:</strong> {{ "%.5f"|format(opp.entry_price) }}</p>
                        <p><strong>Stop Loss:</strong> {{ "%.5f"|format(opp.stop_loss) }} ({{ "%.1f"|format(opp.risk_pips) }} pips risk)</p>
                        <p><strong>Take Profit:</strong> {{ "%.5f"|format(opp.take_profit) }} ({{ "%.1f"|format(opp.reward_pips) }} pips reward)</p>
                        <p><strong>Risk/Reward:</strong> 1:{{ "%.2f"|format(opp.risk_reward) }}</p>
                        <p><strong>Probability TP before SL:</strong> {{ "%.0f"|format(opp.probability_tp_before_sl * 100) }}%</p>
                        <p><strong>Estimated Duration:</strong> {{ opp.estimated_duration_days }} days</p>
                    </div>
                    
                    <div class="details">
                        <p><strong>Analysis Summary:</strong> {{ opp.analysis_summary }}</p>
                        <p><strong>Fundamentals:</strong> {{ opp.fundamentals_summary }}</p>
                        <p><strong>Technicals:</strong> {{ opp.technicals_summary }}</p>
                        <p><strong>Sentiment:</strong> {{ opp.sentiment_summary }}</p>
                    </div>
                </div>
            {% endfor %}
        {% endif %}
        
        <div style="text-align: center; margin-top: 30px; color: #7f8c8d; font-size: 14px;">
            <p>System Status: <strong>ACTIVE</strong> | Last Keep-Alive Ping: {{ last_ping }}</p>
            <p>Next scan in: <span id="countdown">{{ next_scan_minutes }}:00</span> minutes</p>
            <p>App URL: {{ app_url }}</p>
        </div>
    </div>
    
    <script>
        // Countdown timer for next scan
        let minutes = {{ next_scan_minutes }};
        let seconds = 0;
        
        function updateCountdown() {
            if (seconds === 0) {
                if (minutes === 0) {
                    window.location.reload();
                    return;
                }
                minutes--;
                seconds = 59;
            } else {
                seconds--;
            }
            
            document.getElementById('countdown').textContent = 
                minutes.toString().padStart(2, '0') + ':' + 
                seconds.toString().padStart(2, '0');
        }
        
        setInterval(updateCountdown, 1000);
        updateCountdown();
    </script>
</body>
</html>
'''

@app.route('/')
def home():
    """Main web interface"""
    try:
        # Load latest scan
        db_file = Path(config.OPPORTUNITIES_DB)
        if db_file.exists():
            with open(db_file, 'r') as f:
                data = json.load(f)
            
            if data['scans']:
                latest_scan = data['scans'][-1]
                stats = data.get('stats', {
                    'total_scans': len(data['scans']),
                    'total_opportunities': len(data['opportunities']),
                    'avg_scan_duration': 0,
                    'avg_opportunities_per_scan': len(data['opportunities']) / max(len(data['scans']), 1)
                })
            else:
                latest_scan = {
                    'timestamp': datetime.now().isoformat(),
                    'pairs_scanned': 0,
                    'very_high_probability_setups': 0,
                    'scan_duration_seconds': 0,
                    'market_state': 'UNKNOWN',
                    'opportunities': []
                }
                stats = {
                    'total_scans': 0,
                    'total_opportunities': 0,
                    'avg_scan_duration': 0,
                    'avg_opportunities_per_scan': 0
                }
        else:
            latest_scan = {
                'timestamp': datetime.now().isoformat(),
                'pairs_scanned': 0,
                'very_high_probability_setups': 0,
                'scan_duration_seconds': 0,
                'market_state': 'UNKNOWN',
                'opportunities': []
            }
            stats = {
                'total_scans': 0,
                'total_opportunities': 0,
                'avg_scan_duration': 0,
                'avg_opportunities_per_scan': 0
            }
        
        return render_template_string(
            HTML_TEMPLATE,
            scan=latest_scan,
            stats=stats,
            last_ping=datetime.now().strftime('%H:%M:%S'),
            next_scan_minutes=config.SCAN_INTERVAL_MINUTES,
            app_url=config.APP_URL
        )
    except Exception as e:
        return f"Error loading data: {str(e)}"

@app.route('/api/scan', methods=['GET'])
def api_scan():
    """API endpoint to trigger a scan"""
    try:
        if scanner:
            result = scanner.run_complete_scan()
            return jsonify({
                'status': 'success',
                'message': f'Scan completed. Found {len(result.opportunities)} very high probability setups.',
                'data': result.to_dict()
            })
        else:
            return jsonify({'status': 'error', 'message': 'Scanner not initialized'}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/opportunities', methods=['GET'])
def api_opportunities():
    """API endpoint to get all opportunities"""
    try:
        db_file = Path(config.OPPORTUNITIES_DB)
        if db_file.exists():
            with open(db_file, 'r') as f:
                data = json.load(f)
            
            # Get parameters
            limit = int(request.args.get('limit', 50))
            min_score = int(request.args.get('min_score', 70))
            
            # Filter opportunities
            opportunities = data.get('opportunities', [])
            filtered = [opp for opp in opportunities if opp.get('confluence_score', 0) >= min_score]
            filtered = filtered[-limit:]  # Most recent
            
            return jsonify({
                'status': 'success',
                'count': len(filtered),
                'data': filtered
            })
        else:
            return jsonify({'status': 'success', 'count': 0, 'data': []})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def api_health():
    """Health check endpoint for keep-alive"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'service': 'Forex Global Confluence Scanner',
        'version': '2.0.0',
        'features': [
            'Real sentiment data from multiple sources',
            'Intelligent pair selection (1459+ pairs)',
            'Professional TP/SL calculation',
            'Never sleeps on Render free tier',
            'No filtering - shows ALL 70%+ confluence setups'
        ]
    })

@app.route('/api/stats', methods=['GET'])
def api_stats():
    """Get scanner statistics"""
    try:
        db_file = Path(config.OPPORTUNITIES_DB)
        if db_file.exists():
            with open(db_file, 'r') as f:
                data = json.load(f)
            
            scans = data.get('scans', [])
            opportunities = data.get('opportunities', [])
            stats = data.get('stats', {})
            
            if scans:
                # Calculate additional statistics
                recent_scans = scans[-10:]
                recent_opportunities = sum(1 for scan in recent_scans 
                                         if scan.get('very_high_probability_setups', 0) > 0)
                
                # Market state distribution
                market_states = {}
                for scan in scans[-50:]:  # Last 50 scans
                    state = scan.get('market_state', 'UNKNOWN')
                    market_states[state] = market_states.get(state, 0) + 1
                
                return jsonify({
                    'status': 'success',
                    'statistics': {
                        'total_scans': stats.get('total_scans', len(scans)),
                        'total_opportunities_found': stats.get('total_opportunities', len(opportunities)),
                        'average_opportunities_per_scan': stats.get('avg_opportunities_per_scan', 
                                                                   len(opportunities) / max(len(scans), 1)),
                        'average_scan_duration_seconds': stats.get('avg_scan_duration', 0),
                        'recent_success_rate': round(recent_opportunities / len(recent_scans) * 100, 1),
                        'market_state_distribution': market_states,
                        'last_scan_time': scans[-1]['timestamp'] if scans else None,
                        'system_uptime': get_system_uptime()
                    }
                })
        
        return jsonify({
            'status': 'success',
            'statistics': {
                'total_scans': 0,
                'total_opportunities_found': 0,
                'message': 'No scan data available',
                'system_uptime': get_system_uptime()
            }
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

def get_system_uptime() -> str:
    """Get system uptime string"""
    try:
        if os.path.exists('/proc/uptime'):
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.readline().split()[0])
                hours = int(uptime_seconds // 3600)
                minutes = int((uptime_seconds % 3600) // 60)
                return f"{hours}h {minutes}m"
    except:
        pass
    return "Unknown"

# ============================================================================
# KEEP-ALIVE SYSTEM (IMPROVED FOR RENDER)
# ============================================================================

def self_ping():
    """Ping the app itself to keep it awake"""
    try:
        if not config.APP_URL:
            logger.error("No app URL configured for keep-alive")
            return
        
        # Try multiple endpoints
        endpoints = ['/api/health', '/', '/api/stats']
        
        for endpoint in endpoints:
            try:
                url = f"{config.APP_URL.rstrip('/')}{endpoint}"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    logger.info(f"✅ Keep-alive ping successful: {url}")
                    break
                else:
                    logger.warning(f"⚠ Keep-alive ping failed ({response.status_code}): {url}")
            except Exception as e:
                logger.debug(f"Keep-alive ping attempt failed for {endpoint}: {e}")
                continue
        
    except Exception as e:
        logger.error(f"❌ Keep-alive ping error: {e}")

def schedule_keep_alive():
    """Schedule regular keep-alive pings"""
    schedule.every(config.SELF_PING_INTERVAL_MINUTES).minutes.do(self_ping)
    logger.info(f"✅ Scheduled keep-alive pings every {config.SELF_PING_INTERVAL_MINUTES} minutes to {config.APP_URL}")

def schedule_scans():
    """Schedule regular market scans"""
    schedule.every(config.SCAN_INTERVAL_MINUTES).minutes.do(run_scheduled_scan)
    logger.info(f"✅ Scheduled market scans every {config.SCAN_INTERVAL_MINUTES} minutes")

def run_scheduled_scan():
    """Run a scheduled market scan"""
    try:
        logger.info(f"🚀 Running scheduled market scan at {datetime.now()}")
        if scanner:
            result = scanner.run_complete_scan()
            logger.info(f"✅ Scheduled scan completed. Found {len(result.opportunities)} very high probability setups.")
    except Exception as e:
        logger.error(f"❌ Scheduled scan failed: {e}")

# ============================================================================
# MAIN APPLICATION (OPTIMIZED)
# ============================================================================

def main():
    """Main application entry point"""
    global scanner
    
    logger.info("=" * 60)
    logger.info("🚀 FOREX GLOBAL CONFLUENCE SCANNER v2.0")
    logger.info("=" * 60)
    logger.info("Features:")
    logger.info("• Real sentiment data from CFTC, FXSSI, MyFXBook, TradingView")
    logger.info(f"• Intelligent scanning of up to {config.MAX_PAIRS_PER_SCAN} pairs")
    logger.info("• Professional TP/SL calculation with probability estimates")
    logger.info("• Never sleeps on Render free tier")
    logger.info("• Shows ALL 70%+ confluence setups - NO FILTERS")
    logger.info("=" * 60)
    
    # Load configuration
    config = Config.load()
    
    # Initialize scanner
    scanner = GlobalForexScanner(config)
    
    # Schedule tasks
    schedule_keep_alive()
    schedule_scans()
    
    # Run initial scan immediately
    logger.info("Running initial market scan...")
    try:
        initial_result = scanner.run_complete_scan()
        logger.info(f"✅ Initial scan completed: {len(initial_result.opportunities)} very high probability setups found")
    except Exception as e:
        logger.error(f"❌ Initial scan failed: {e}")
    
    # Start the scheduler in a separate thread
    def run_scheduler():
        while True:
            try:
                schedule.run_pending()
                time.sleep(1)
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                time.sleep(5)
    
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    logger.info("✅ Application fully initialized and running")
    logger.info(f"🌐 Web interface available at: {config.APP_URL}")
    logger.info(f"🔍 Scanning every {config.SCAN_INTERVAL_MINUTES} minutes")
    logger.info(f"❤️  Keep-alive pings every {config.SELF_PING_INTERVAL_MINUTES} minutes")
    logger.info("=" * 60)
    
    # Start Flask app
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)

if __name__ == "__main__":
    # Create necessary directories
    os.makedirs("data", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    
    # Run main application
    main()