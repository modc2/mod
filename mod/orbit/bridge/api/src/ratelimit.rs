// Sliding-window per-IP rate limiter. Stored in memory; resets on restart.
//
// Window is a fixed number of seconds; we keep a deque of timestamps per IP
// and prune expired entries on every request.

use parking_lot::Mutex;
use std::{
    collections::{HashMap, VecDeque},
    net::IpAddr,
    time::{Duration, Instant},
};

pub struct RateLimiter {
    window: Duration,
    max_requests: usize,
    state: Mutex<HashMap<IpAddr, VecDeque<Instant>>>,
}

impl RateLimiter {
    pub fn new(window_secs: u64, max_requests: usize) -> Self {
        Self {
            window: Duration::from_secs(window_secs),
            max_requests,
            state: Mutex::new(HashMap::new()),
        }
    }

    /// Returns `true` if the request is allowed, `false` if it should be 429'd.
    pub fn check(&self, ip: IpAddr) -> bool {
        let now = Instant::now();
        let mut g = self.state.lock();

        // Periodic full sweep to keep the map bounded — done if we crossed the
        // 1024 IP threshold to amortize the cost.
        if g.len() > 1024 {
            g.retain(|_, dq| {
                while dq.front().map(|t| now.duration_since(*t) > self.window).unwrap_or(false) {
                    dq.pop_front();
                }
                !dq.is_empty()
            });
        }

        let dq = g.entry(ip).or_default();
        while dq.front().map(|t| now.duration_since(*t) > self.window).unwrap_or(false) {
            dq.pop_front();
        }
        if dq.len() >= self.max_requests {
            return false;
        }
        dq.push_back(now);
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::Ipv4Addr;

    #[test]
    fn limits_per_ip() {
        let r = RateLimiter::new(60, 3);
        let ip = IpAddr::V4(Ipv4Addr::new(127, 0, 0, 1));
        assert!(r.check(ip));
        assert!(r.check(ip));
        assert!(r.check(ip));
        assert!(!r.check(ip)); // 4th request blocked
    }

    #[test]
    fn isolates_ips() {
        let r = RateLimiter::new(60, 1);
        let a = IpAddr::V4(Ipv4Addr::new(1, 1, 1, 1));
        let b = IpAddr::V4(Ipv4Addr::new(2, 2, 2, 2));
        assert!(r.check(a));
        assert!(!r.check(a));
        assert!(r.check(b)); // independent
    }
}
