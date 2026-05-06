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
const startedAt  = Date.now();

let latestMetrics  = null;
let latestMetricsAt = null;
let receiverState  = {
  running: false,
  pid: null,
  restarts: 0,
  lastExitCode: null,
  lastStartedAt: null,
  lastExitedAt: null,
};

// ── Middleware ─────────────────────────────────────────────────────────────────
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// ── Routes ────────────────────────────────────────────────────────────────────
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// Metrics endpoint — called by receiver.py every second
app.post('/metrics', (req, res) => {
  latestMetrics = req.body;
  latestMetricsAt = Date.now();
  io.emit('metrics', req.body);
  res.sendStatus(200);
});

app.get('/health', (_req, res) => {
  const now = Date.now();
  res.json({
    ok: true,
    uptime_s: Math.round((now - startedAt) / 1000),
    latest_metrics_age_s: latestMetricsAt ? Math.round((now - latestMetricsAt) / 1000) : null,
    receiver: receiverState,
  });
});

// ── Socket.IO ─────────────────────────────────────────────────────────────────
io.on('connection', (socket) => {
  console.log(`[Dashboard] Browser connected  (id=${socket.id})`);
  if (latestMetrics) {
    socket.emit('metrics', latestMetrics);
  }
  socket.on('disconnect', () => {
    console.log(`[Dashboard] Browser disconnected (id=${socket.id})`);
  });
});

// ── Start receiver.py as a subprocess ────────────────────────────────────────
const PORT = Number(process.env.PORT || 3000);
httpServer.listen(PORT, () => {
  console.log(`Dashboard    → http://localhost:${PORT}`);

  const python       = os.platform() === 'win32' ? 'python' : 'python3';
  const receiverPath = path.join(__dirname, 'receiver.py');
  let   currentReceiver = null;
  let   shuttingDown    = false;

  function spawnReceiver() {
    if (shuttingDown) return;
    currentReceiver = spawn(python, [receiverPath], { stdio: 'inherit' });
    receiverState = {
      ...receiverState,
      running: true,
      pid: currentReceiver.pid,
      restarts: receiverState.restarts + 1,
      lastStartedAt: new Date().toISOString(),
    };
    console.log(`[Receiver]   Started (${python} receiver.py)`);

    currentReceiver.on('error', (err) => {
      receiverState = { ...receiverState, running: false, pid: null };
      console.error(`[Receiver] Failed to start: ${err.message}`);
    });

    currentReceiver.on('close', (code) => {
      receiverState = {
        ...receiverState,
        running: false,
        pid: null,
        lastExitCode: code,
        lastExitedAt: new Date().toISOString(),
      };
      if (shuttingDown) return;
      console.log(`[Receiver] Exited with code ${code} — restarting in 3s`);
      setTimeout(spawnReceiver, 3000);
    });
  }

  spawnReceiver();

  process.on('SIGINT',  () => { shuttingDown = true; currentReceiver?.kill(); process.exit(); });
  process.on('SIGTERM', () => { shuttingDown = true; currentReceiver?.kill(); process.exit(); });
});
