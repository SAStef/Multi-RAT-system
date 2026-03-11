/**
 * Multi-RAT Express Dashboard
 *
 * Receives metric snapshots from receiver.py via HTTP POST /metrics
 * and broadcasts them to all connected browsers via Socket.IO.
 *
 * Install:  npm install
 * Run:      node server.js
 * Open:     http://localhost:3000
 */

const express   = require('express');
const http      = require('http');
const { Server } = require('socket.io');
const path      = require('path');

const app        = express();
const httpServer = http.createServer(app);
const io         = new Server(httpServer);

// ── Middleware ─────────────────────────────────────────────────────────────────
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// ── Routes ─────────────────────────────────────────────────────────────────────

// Dashboard
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// Metrics endpoint — called by receiver.py every second
app.post('/metrics', (req, res) => {
  io.emit('metrics', req.body);   // broadcast to all connected browsers
  res.sendStatus(200);
});

// ── Socket.IO ──────────────────────────────────────────────────────────────────
io.on('connection', (socket) => {
  console.log(`[Dashboard] Browser connected  (id=${socket.id})`);
  socket.on('disconnect', () => {
    console.log(`[Dashboard] Browser disconnected (id=${socket.id})`);
  });
});

// ── Start ──────────────────────────────────────────────────────────────────────
const PORT = 3000;
httpServer.listen(PORT, () => {
  console.log(`Dashboard → http://localhost:${PORT}`);
  console.log('Waiting for metrics from receiver.py...');
});
