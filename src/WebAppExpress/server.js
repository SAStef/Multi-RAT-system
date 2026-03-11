/**
 * Multi-RAT Express Dashboard
 *
 * Automatically starts receiver.py as a subprocess, so you only need:
 *   node server.js          (terminal 1 — starts server + receiver)
 *   python3 Sender.py       (terminal 2 — sends packets)
 *
 * Open: http://localhost:3000
 */

const express    = require('express');
const http       = require('http');
const { Server } = require('socket.io');
const path       = require('path');
const { spawn }  = require('child_process');
const os         = require('os');

const app        = express();
const httpServer = http.createServer(app);
const io         = new Server(httpServer);

// ── Middleware ─────────────────────────────────────────────────────────────────
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// ── Routes ────────────────────────────────────────────────────────────────────
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// Metrics endpoint — called by receiver.py every second
app.post('/metrics', (req, res) => {
  io.emit('metrics', req.body);
  res.sendStatus(200);
});

// ── Socket.IO ─────────────────────────────────────────────────────────────────
io.on('connection', (socket) => {
  console.log(`[Dashboard] Browser connected  (id=${socket.id})`);
  socket.on('disconnect', () => {
    console.log(`[Dashboard] Browser disconnected (id=${socket.id})`);
  });
});

// ── Start receiver.py as a subprocess ────────────────────────────────────────
const PORT = 3000;
httpServer.listen(PORT, () => {
  console.log(`Dashboard    → http://localhost:${PORT}`);

  // Use 'python' on Windows, 'python3' on macOS/Linux
  const python = os.platform() === 'win32' ? 'python' : 'python3';
  const receiverPath = path.join(__dirname, 'receiver.py');

  const receiver = spawn(python, [receiverPath], { stdio: 'inherit' });

  receiver.on('error', (err) => {
    console.error(`[Receiver] Failed to start: ${err.message}`);
  });

  receiver.on('close', (code) => {
    if (code !== 0) console.log(`[Receiver] Exited with code ${code}`);
  });

  // Kill receiver when server shuts down
  process.on('SIGINT', () => {
    receiver.kill();
    process.exit();
  });

  console.log(`[Receiver]   Started (${python} receiver.py)`);
});
