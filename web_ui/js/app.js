import { Chessground } from './chessground.min.js';

// --- State Variables ---
let currentOrientation = 'white';
let soundEnabled = true;
let audioCtx = null;
let latestFen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
let currentBestMove = null;
let currentEvalScore = '0.0';

// --- Initialize Chess.js ---
const chess = new window.Chess();

// --- Initialize Chessground UI ---
const boardContainer = document.getElementById('chessgroundBoard');
const ground = Chessground(boardContainer, {
  orientation: currentOrientation,
  coordinates: true,
  viewOnly: true,
  drawable: {
    enabled: true,
    visible: true,
    shapes: []
  },
  highlight: {
    lastMove: true,
    check: true
  }
});

// --- Web Audio Synthesizer (Zero MP3 files needed) ---
function getAudioContext() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioCtx.state === 'suspended') {
    audioCtx.resume();
  }
  return audioCtx;
}

function playMoveSound(isCapture = false) {
  if (!soundEnabled) return;
  try {
    const ctx = getAudioContext();
    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.type = isCapture ? 'triangle' : 'sine';
    osc.frequency.setValueAtTime(isCapture ? 460 : 340, now);
    osc.frequency.exponentialRampToValueAtTime(100, now + 0.08);

    gain.gain.setValueAtTime(0.25, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.08);

    osc.connect(gain);
    gain.connect(ctx.destination);

    osc.start(now);
    osc.stop(now + 0.08);
  } catch (e) {
    // Audio may be blocked before initial user gesture
  }
}

// --- Stockfish Web Worker Integration ---
const engine = new Worker('js/stockfish.js');
engine.postMessage('uci');
engine.postMessage('isready');
engine.postMessage('setoption name Hash value 32');

engine.onmessage = (event) => {
  const line = typeof event.data === 'string' ? event.data : '';
  if (!line) return;

  // Stockfish evaluation score & depth
  if (line.startsWith('info') && (line.includes('score cp') || line.includes('score mate'))) {
    handleEngineInfo(line);
  }

  // Stockfish best move recommendation
  if (line.startsWith('bestmove')) {
    const parts = line.split(' ');
    const move = parts[1];
    if (move && move !== '(none)') {
      handleBestMove(move);
    }
  }
};

function handleEngineInfo(line) {
  // Extract search depth
  const depthMatch = line.match(/depth (\d+)/);
  if (depthMatch) {
    document.getElementById('engineDepth').innerText = depthMatch[1];
  }

  // Extract NPS
  const npsMatch = line.match(/nps (\d+)/);
  if (npsMatch) {
    const nps = parseInt(npsMatch[1], 10);
    document.getElementById('engineNps').innerText = (nps / 1000).toFixed(0) + 'k';
  }

  // Extract Nodes
  const nodesMatch = line.match(/nodes (\d+)/);
  if (nodesMatch) {
    document.getElementById('engineNodes').innerText = parseInt(nodesMatch[1], 10).toLocaleString();
  }

  // Extract centipawn score
  const cpMatch = line.match(/score cp (-?\d+)/);
  if (cpMatch) {
    let cp = parseInt(cpMatch[1], 10);
    // Stockfish outputs score relative to turn; convert to White perspective
    if (chess.turn() === 'b') {
      cp = -cp;
    }
    const scoreVal = (cp / 100).toFixed(1);
    currentEvalScore = (cp > 0 ? '+' : '') + scoreVal;
    updateEvalBar(cp, false);
  }

  // Extract mate score
  const mateMatch = line.match(/score mate (-?\d+)/);
  if (mateMatch) {
    let mate = parseInt(mateMatch[1], 10);
    if (chess.turn() === 'b') {
      mate = -mate;
    }
    currentEvalScore = 'M' + Math.abs(mate);
    updateEvalBar(mate, true);
  }

  // Extract principal variation (PV)
  const pvIndex = line.indexOf(' pv ');
  if (pvIndex !== -1) {
    const pvMoves = line.substring(pvIndex + 4).trim().split(' ').slice(0, 6).join(' ');
    document.getElementById('pvLine').innerText = pvMoves;
  }
}

function handleBestMove(moveStr) {
  currentBestMove = moveStr;
  const orig = moveStr.slice(0, 2);
  const dest = moveStr.slice(2, 4);

  // Render green vector arrow natively in Chessground
  ground.setShapes([{
    orig: orig,
    dest: dest,
    brush: 'green'
  }]);

  document.getElementById('bestMoveBadge').innerText = `${orig} ➔ ${dest}`;
  document.getElementById('evalBadge').innerText = currentEvalScore;
  document.getElementById('engineStatus').innerText = '● Search Complete';
}

function updateEvalBar(score, isMate) {
  const whiteBar = document.getElementById('evalBarWhite');
  const scoreText = document.getElementById('evalScoreText');
  let whitePercent = 50;

  if (isMate) {
    whitePercent = score > 0 ? 98 : 2;
  } else {
    // Sigmoid curve: 1 / (1 + 10^(-cp/400))
    const p = 1 / (1 + Math.pow(10, -score / 400));
    whitePercent = Math.max(5, Math.min(95, p * 100));
  }

  whiteBar.style.height = `${whitePercent}%`;
  scoreText.innerText = currentEvalScore;

  if (whitePercent < 45) {
    scoreText.classList.add('black-lead');
  } else {
    scoreText.classList.remove('black-lead');
  }
}

function triggerEngineAnalysis(fen) {
  document.getElementById('engineStatus').innerText = '● Calculating';
  engine.postMessage('stop');
  engine.postMessage(`position fen ${fen}`);
  engine.postMessage('go depth 16');
}

// --- Update Board from Server State ---
let isDemoMode = false;

const gantryLabels = { EVACUATE: 'EVACUATE', MOVE_MAIN: 'MOVE', RESTORE: 'RESTORE', HOME_GANTRY: 'HOME' };
const gantryColors = { EVACUATE: '#e5b342', MOVE_MAIN: '#70c025', RESTORE: '#56b6c2', HOME_GANTRY: '#c678dd' };

function showGantryAction(ga) {
  document.getElementById('bestMoveBadge').innerText = 'Step ' + ga.step;
  const badge = document.getElementById('evalBadge');
  badge.innerText = gantryLabels[ga.type] || ga.type;
  badge.style.color = gantryColors[ga.type] || '#fff';
  document.getElementById('pvLine').innerText = ga.piece + ' — ' + (ga.detail || '');
  document.getElementById('engineStatus').innerText = '● Executing';
}

function parseNativeEval(evalStr) {
  const s = evalStr.trim();
  if (s.includes('M') || s.includes('#')) {
    const n = parseInt(s.replace(/[^0-9]/g, '')) || 1;
    const sign = s.startsWith('-') ? -1 : 1;
    updateEvalBar(sign * n, true);
  } else {
    const cp = Math.round(parseFloat(s) * 100) || 0;
    updateEvalBar(cp, false);
  }
}

function applyGameState(state) {
  if (!state || !state.fen) return;
  const newFen = state.fen;
  latestFen = newFen;

  document.getElementById('fenDisplay').innerText = newFen;

  const lastMove = state.last_move ? [state.last_move.from, state.last_move.to] : [];

  ground.set({
    fen: newFen,
    lastMove: lastMove
  });

  if (state.last_move) {
    playMoveSound(!!state.last_move.captured);
  }

  // --- Gantry execution (works in all modes) ---
  if (state.gantry_action) {
    engine.postMessage('stop');
    ground.setShapes([]);

    if (state.demo_mode && !isDemoMode) {
      isDemoMode = true;
      document.querySelector('h1').innerText = 'Automated Chessboard — Demo';
      document.querySelector('.eval-bar-wrapper').style.display = 'none';
      document.getElementById('difficultyPanel').style.display = 'none';
    }

    document.querySelector('.engine-stats-row').style.display = 'none';
    document.getElementById('turnIndicator').innerText = state.demo_mode ? 'Demo Mode' : 'Gantry Moving';
    showGantryAction(state.gantry_action);

    if (state.history && Array.isArray(state.history)) {
      renderMoveHistory(state.history);
    }
    return;
  }

  // --- Demo mode idle ---
  if (state.demo_mode) {
    if (!isDemoMode) {
      isDemoMode = true;
      document.querySelector('h1').innerText = 'Automated Chessboard — Demo';
      document.querySelector('.eval-bar-wrapper').style.display = 'none';
      document.getElementById('difficultyPanel').style.display = 'none';
    }
    engine.postMessage('stop');
    document.querySelector('.engine-stats-row').style.display = 'none';
    ground.setShapes([]);
    document.getElementById('turnIndicator').innerText = 'Demo Mode';
    document.getElementById('engineStatus').innerText = '● Ready';
    document.getElementById('bestMoveBadge').innerText = '—';
    document.getElementById('evalBadge').innerText = '';
    document.getElementById('evalBadge').style.color = '';
    document.getElementById('pvLine').innerText = 'Awaiting move input in terminal...';
    return;
  }

  // --- Full game mode ---
  if (isDemoMode) {
    isDemoMode = false;
    document.querySelector('h1').innerText = 'Physical Chessboard Analysis Workbench';
    document.querySelector('.eval-bar-wrapper').style.display = '';
    document.getElementById('difficultyPanel').style.display = '';
  }
  document.querySelector('.engine-stats-row').style.display = '';
  document.getElementById('turnIndicator').innerText = state.turn === 'white' ? "White to move" : "Black to move";

  if (state.engine_move) {
    engine.postMessage('stop');
    currentEvalScore = state.engine_eval || '0.0';
    handleBestMove(state.engine_move);
    document.getElementById('evalBadge').innerText = currentEvalScore;
    document.getElementById('engineDepth').innerText = state.engine_depth || '';
    document.getElementById('engineNps').innerText = '—';
    document.getElementById('engineNodes').innerText = '—';
    document.getElementById('engineStatus').innerText = '● Native Stockfish';
    parseNativeEval(currentEvalScore);
  } else {
    const validation = chess.validate_fen(newFen);
    if (validation.valid) {
      chess.load(newFen);
      triggerEngineAnalysis(newFen);
    } else {
      ground.setShapes([]);
      document.getElementById('bestMoveBadge').innerText = '--';
      document.getElementById('evalBadge').innerText = 'Partial';
      document.getElementById('evalBadge').style.color = '';
      document.getElementById('pvLine').innerText = 'Partial bench setup active (Place both kings to start engine analysis)';
      document.getElementById('engineStatus').innerText = '○ Standby';
    }
  }

  if (state.history && Array.isArray(state.history)) {
    renderMoveHistory(state.history);
  }

  if (state.difficulty) {
    updateDifficultyDisplay(state.difficulty);
  }
}

function renderMoveHistory(history) {
  const tbody = document.getElementById('movesTableBody');
  tbody.innerHTML = '';
  const rows = [];
  
  history.forEach(h => {
    let row = rows[h.fullmove - 1];
    if (!row) {
      row = { num: h.fullmove, w: '--', b: '--' };
      rows[h.fullmove - 1] = row;
    }
    if (h.turn === 'white') row.w = h.san;
    else row.b = h.san;
  });

  if (rows.length === 0) {
    tbody.innerHTML = '<tr><td class="move-num">1.</td><td class="move-w">--</td><td class="move-b">--</td></tr>';
    return;
  }

  rows.forEach(r => {
    if (!r) return;
    const tr = document.createElement('tr');
    tr.innerHTML = `<td class="move-num">${r.num}.</td><td class="move-w">${r.w}</td><td class="move-b">${r.b}</td>`;
    tbody.appendChild(tr);
  });

  const scrollWrapper = document.querySelector('.moves-table-wrapper');
  scrollWrapper.scrollTop = scrollWrapper.scrollHeight;
}

// --- Real-time Server-Sent Events (SSE) ---
let sseActive = false;

function setupEventSource() {
  const evtSource = new EventSource('/events');

  evtSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      applyGameState(data);
      setConnectionStatus(true);
    } catch (err) {
      console.error("SSE parse error:", err);
    }
  };

  evtSource.onopen = () => {
    sseActive = true;
    setConnectionStatus(true);
  };

  evtSource.onerror = () => {
    sseActive = false;
    setConnectionStatus(false);
  };
}

function setConnectionStatus(connected) {
  const tag = document.getElementById('connectionTag');
  const text = document.getElementById('connectionText');
  if (connected) {
    tag.className = 'status-tag connected';
    text.innerText = 'Hardware Link Active';
  } else {
    tag.className = 'status-tag';
    text.innerText = 'Connecting...';
  }
}

// Fallback polling loop (handles initial load and SSE drops)
async function pollState() {
  if (sseActive) return;
  try {
    const res = await fetch('/api/state');
    if (res.ok) {
      const data = await res.json();
      applyGameState(data);
      setConnectionStatus(true);
    }
  } catch (err) {
    setConnectionStatus(false);
  }
}

// --- Difficulty Selector ---
function updateDifficultyDisplay(info) {
  if (!info) return;
  const statusEl = document.getElementById('difficultyStatus');
  statusEl.innerText = `Skill ${info.skill} · Depth ${info.depth}`;

  document.querySelectorAll('.diff-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.preset === info.preset);
  });
}

document.querySelectorAll('.diff-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const preset = btn.dataset.preset;
    document.querySelectorAll('.diff-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('difficultyStatus').innerText = 'Applying...';

    try {
      const res = await fetch('/api/difficulty', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preset }),
      });
      const data = await res.json();
      if (data.ok) {
        updateDifficultyDisplay(data);
      } else {
        document.getElementById('difficultyStatus').innerText = data.error || 'Failed';
      }
    } catch (err) {
      document.getElementById('difficultyStatus').innerText = 'Connection error';
    }
  });
});

// Start real-time stream and initial fetch
setupEventSource();
pollState();
setInterval(pollState, 1000);

