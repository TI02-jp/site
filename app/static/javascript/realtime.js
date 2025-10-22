/**
 * Real-time synchronization client using Server-Sent Events (SSE)
 * Provides automatic updates across all clients without page reloads
 */

(function() {
  'use strict';

  class RealtimeClient {
    constructor(options = {}) {
      this.scopes = options.scopes || ['all'];
      this.reconnectInterval = options.reconnectInterval || 3000;
      this.maxReconnectAttempts = options.maxReconnectAttempts || Infinity;

      this.eventSource = null;
      this.reconnectAttempts = 0;
      this.isConnected = false;
      this.handlers = {};
      this.connectionStateListeners = [];

      // Bind methods
      this.connect = this.connect.bind(this);
      this.disconnect = this.disconnect.bind(this);
      this.handleMessage = this.handleMessage.bind(this);
      this.handleOpen = this.handleOpen.bind(this);
      this.handleError = this.handleError.bind(this);
    }

    /**
     * Connect to the real-time stream
     */
    connect() {
      if (this.eventSource) {
        console.warn('[Realtime] Already connected');
        return;
      }

      const scopesParam = this.scopes.join(',');
      const url = `/realtime/stream?scopes=${encodeURIComponent(scopesParam)}`;

      console.log(`[Realtime] Connecting to ${url}...`);

      try {
        this.eventSource = new EventSource(url);

        this.eventSource.addEventListener('open', this.handleOpen);
        this.eventSource.addEventListener('error', this.handleError);
        this.eventSource.addEventListener('message', this.handleMessage);
      } catch (error) {
        console.error('[Realtime] Failed to create EventSource:', error);
        this.scheduleReconnect();
      }
    }

    /**
     * Disconnect from the real-time stream
     */
    disconnect() {
      console.log('[Realtime] Disconnecting...');

      if (this.eventSource) {
        this.eventSource.close();
        this.eventSource = null;
      }

      this.isConnected = false;
      this.reconnectAttempts = 0;
      this.notifyConnectionState('disconnected');
    }

    /**
     * Handle connection opened
     */
    handleOpen() {
      console.log('[Realtime] Connected successfully');
      this.isConnected = true;
      this.reconnectAttempts = 0;
      this.notifyConnectionState('connected');
    }

    /**
     * Handle connection error
     */
    handleError(error) {
      console.error('[Realtime] Connection error:', error);

      this.isConnected = false;
      this.notifyConnectionState('error');

      if (this.eventSource) {
        this.eventSource.close();
        this.eventSource = null;
      }

      this.scheduleReconnect();
    }

    /**
     * Schedule a reconnection attempt
     */
    scheduleReconnect() {
      if (this.reconnectAttempts >= this.maxReconnectAttempts) {
        console.error('[Realtime] Max reconnection attempts reached');
        this.notifyConnectionState('failed');
        return;
      }

      this.reconnectAttempts++;
      const delay = Math.min(this.reconnectInterval * this.reconnectAttempts, 30000);

      console.log(`[Realtime] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})...`);
      this.notifyConnectionState('reconnecting', { attempt: this.reconnectAttempts, delay });

      setTimeout(() => {
        this.connect();
      }, delay);
    }

    /**
     * Handle incoming message
     */
    handleMessage(event) {
      try {
        const payload = JSON.parse(event.data);
        const { type, data, timestamp, scope } = payload;

        console.log(`[Realtime] Received event: ${type}`, data);

        // Trigger registered handlers for this event type
        const handlers = this.handlers[type] || [];
        handlers.forEach(handler => {
          try {
            handler(data, { type, timestamp, scope });
          } catch (error) {
            console.error(`[Realtime] Handler error for ${type}:`, error);
          }
        });

        // Also trigger wildcard handlers
        const wildcardHandlers = this.handlers['*'] || [];
        wildcardHandlers.forEach(handler => {
          try {
            handler(data, { type, timestamp, scope });
          } catch (error) {
            console.error('[Realtime] Wildcard handler error:', error);
          }
        });
      } catch (error) {
        console.error('[Realtime] Failed to parse message:', error);
      }
    }

    /**
     * Register an event handler
     * @param {string} eventType - Event type to listen for (e.g., 'task:created')
     * @param {function} handler - Handler function
     */
    on(eventType, handler) {
      if (typeof handler !== 'function') {
        throw new Error('Handler must be a function');
      }

      if (!this.handlers[eventType]) {
        this.handlers[eventType] = [];
      }

      this.handlers[eventType].push(handler);

      // Return unsubscribe function
      return () => {
        this.off(eventType, handler);
      };
    }

    /**
     * Unregister an event handler
     * @param {string} eventType - Event type
     * @param {function} handler - Handler function to remove
     */
    off(eventType, handler) {
      if (!this.handlers[eventType]) {
        return;
      }

      this.handlers[eventType] = this.handlers[eventType].filter(h => h !== handler);
    }

    /**
     * Register a connection state listener
     * @param {function} listener - Listener function(state, meta)
     */
    onConnectionState(listener) {
      if (typeof listener !== 'function') {
        throw new Error('Listener must be a function');
      }

      this.connectionStateListeners.push(listener);

      // Return unsubscribe function
      return () => {
        this.connectionStateListeners = this.connectionStateListeners.filter(l => l !== listener);
      };
    }

    /**
     * Notify all connection state listeners
     */
    notifyConnectionState(state, meta = {}) {
      this.connectionStateListeners.forEach(listener => {
        try {
          listener(state, meta);
        } catch (error) {
          console.error('[Realtime] Connection state listener error:', error);
        }
      });
    }

    /**
     * Check if client is currently connected
     */
    get connected() {
      return this.isConnected;
    }
  }

  // Export to global scope
  window.RealtimeClient = RealtimeClient;

  // Auto-initialize realtime client if page has data-realtime attribute
  document.addEventListener('DOMContentLoaded', () => {
    const realtimeElement = document.querySelector('[data-realtime]');

    if (realtimeElement) {
      const scopesAttr = realtimeElement.getAttribute('data-realtime-scopes');
      const scopes = scopesAttr ? scopesAttr.split(',').map(s => s.trim()) : ['all'];

      console.log('[Realtime] Auto-initializing with scopes:', scopes);

      const client = new RealtimeClient({ scopes });

      // Expose to window for manual access
      window.realtimeClient = client;

      // Connect automatically
      client.connect();

      // Show connection status (optional)
      client.onConnectionState((state, meta) => {
        console.log(`[Realtime] Connection state: ${state}`, meta);

        // You can add visual indicators here
        // For example, show a toast when reconnecting
        if (state === 'reconnecting') {
          console.log(`[Realtime] Reconnecting (attempt ${meta.attempt})...`);
        } else if (state === 'connected') {
          console.log('[Realtime] Connected to real-time updates');
        } else if (state === 'error') {
          console.warn('[Realtime] Connection error, will retry...');
        }
      });

      // Disconnect on page unload
      window.addEventListener('beforeunload', () => {
        client.disconnect();
      });
    }
  });
})();
