//! Event handling for the Subspace Explorer TUI

use crossterm::event::{self, Event as CEvent, KeyEvent};
use std::time::Duration;

/// Represents an event in the application
#[derive(Debug, Clone, Copy)]
pub enum Event {
    /// Terminal tick event
    Tick,
    /// Key press event
    Key(KeyEvent),
    /// Terminal resize event
    Resize(u16, u16),
}

/// An event handler for the application
pub struct EventHandler {
    /// Event receiver channel
    receiver: tokio::sync::mpsc::UnboundedReceiver<Event>,
    /// Event sender channel
    _sender: tokio::sync::mpsc::UnboundedSender<Event>,
}

impl EventHandler {
    /// Create a new event handler with the specified tick rate
    pub fn new(tick_rate: u64) -> Self {
        let (sender, receiver) = tokio::sync::mpsc::unbounded_channel();
        let event_sender = sender.clone();

        // Spawn a task to handle events
        tokio::spawn(async move {
            let tick_delay = tokio::time::Duration::from_millis(tick_rate);
            let mut tick = tokio::time::interval(tick_delay);

            loop {
                let tick_delay = tick.tick();
                let crossterm_event =
                    tokio::task::spawn_blocking(|| event::poll(Duration::from_millis(100)));

                tokio::select! {
                    _ = tick_delay => {
                        if let Err(e) = event_sender.send(Event::Tick) {
                            log::error!("Failed to send tick event: {}", e);
                            break;
                        }
                    }
                    res = crossterm_event => match res {
                        Ok(Ok(has_event)) => {
                            if has_event {
                                if let Ok(evt) = event::read() {
                                    match evt {
                                        CEvent::Key(key) => {
                                            if let Err(e) = event_sender.send(Event::Key(key)) {
                                                log::error!("Failed to send key event: {}", e);
                                                break;
                                            }
                                        }
                                        CEvent::Resize(x, y) => {
                                            if let Err(e) = event_sender.send(Event::Resize(x, y)) {
                                                log::error!("Failed to send resize event: {}", e);
                                                break;
                                            }
                                        }
                                        _ => {}
                                    }
                                }
                            }
                        }
                        Ok(Err(e)) => {
                            log::error!("Error reading crossterm event: {}", e);
                        }
                        Err(e) => {
                            log::error!("Error polling crossterm event: {}", e);
                            break;
                        }
                    }
                }
            }
        });

        Self {
            receiver,
            _sender: sender,
        }
    }

    /// Get the next event from the event handler
    pub async fn next(&mut self) -> Option<Event> {
        self.receiver.recv().await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

    #[tokio::test]
    async fn test_event_handler_initialization() {
        let mut handler = EventHandler::new(100);
        assert!(handler.next().await.is_some());
    }

    #[test]
    fn test_event_creation() {
        let tick = Event::Tick;
        let key = Event::Key(KeyEvent::new(KeyCode::Char('q'), KeyModifiers::NONE));
        let resize = Event::Resize(80, 24);

        assert!(matches!(tick, Event::Tick));
        assert!(matches!(key, Event::Key(_)));
        assert!(matches!(resize, Event::Resize(_, _)));
    }
}
