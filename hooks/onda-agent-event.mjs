#!/usr/bin/env node
/**
 * onda-agent-event.mjs — Agent Team OS → terminal driver bridge.
 *
 * One hook registered on several Claude Code events. Reads the hook payload on
 * stdin, resolves the agent from cwd, and — when running inside a driver
 * terminal — writes one `agent.event` JSON-RPC notification to the driver's
 * unix socket.
 *
 * Contract: ../schema/agent-event.schema.json and ../schema/CONTRACT.md.
 *
 * NON-NEGOTIABLE INVARIANTS
 *
 *   1. Never blocks Claude Code. The socket write is fire-and-forget with a
 *      hard timeout, and the process always exits 0 — on a missing socket, a
 *      refused connection, malformed input, or an outright bug. A hook that
 *      slows down or fails a turn is a bug that kills the feature, so there is
 *      no error path that surfaces to the user.
 *
 *   2. Zero dependencies. Runs on the node that ships with Claude Code: only
 *      node: builtins, no jq, no curl, no package.json.
 *
 *   3. Never leaks content. Tool names and cwd-relative paths only. No file
 *      contents, no prompt text, no Bash command strings.
 *
 * Presence discriminant is ONDA_SOCKET, not ONDA_TERMINAL: the latter is only
 * exported for zsh with a resolved ZDOTDIR, so it is absent under bash/fish
 * (see CONTRACT.md §1).
 */

import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

/** Hard ceiling on the socket write. */
const WRITE_TIMEOUT_MS = 200;

/** Hard ceiling on reading stdin, so a stuck pipe cannot hang the turn. */
const STDIN_TIMEOUT_MS = 150;

const AB_HOME = process.env.AB_HOME || path.join(os.homedir(), '.agent-team-os');

/** Events we forward. Anything else is ignored silently. */
const FORWARDED = new Set([
  'SessionStart',
  'UserPromptSubmit',
  'PreToolUse',
  'PostToolUse',
  'Notification',
  'Stop',
  'SessionEnd',
  'SubagentStart',
  'SubagentStop',
]);

/**
 * Tools whose target is user-authored text rather than a path. For these the
 * target is dropped: a Bash command line can contain anything, including
 * secrets, and it is exactly the kind of content invariant 3 forbids.
 */
const OPAQUE_TOOLS = new Set(['Bash', 'BashOutput', 'Task', 'WebFetch', 'WebSearch']);

function readStdin() {
  return new Promise((resolve) => {
    let data = '';
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      resolve(data);
    };
    const timer = setTimeout(finish, STDIN_TIMEOUT_MS);
    timer.unref?.();
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => {
      data += chunk;
      // Hook payloads are small; cap to avoid unbounded buffering.
      if (data.length > 256 * 1024) finish();
    });
    process.stdin.on('end', finish);
    process.stdin.on('error', finish);
  });
}

function readJsonFile(file) {
  try {
    const raw = fs.readFileSync(file, 'utf8');
    if (!raw.trim()) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Resolve the agent id from cwd via AGENT_MAP.rules[], mirroring
 * `ab_detect_agent`: longest matching prefix wins, then the map's fallback.
 */
function detectAgent(cwd) {
  const map = readJsonFile(path.join(AB_HOME, 'AGENT_MAP.json'));
  if (!map || !cwd) return { map: null, id: null };

  let bestId = null;
  let bestLen = -1;
  for (const rule of Array.isArray(map.rules) ? map.rules : []) {
    if (!rule || typeof rule.pattern !== 'string') continue;
    const pattern = rule.pattern.replace(/\/+$/, '');
    const match = rule.match === 'exact' ? cwd === pattern : cwd === pattern || cwd.startsWith(pattern + '/');
    if (match && pattern.length > bestLen) {
      bestLen = pattern.length;
      bestId = rule.agent;
    }
  }
  return { map, id: bestId || map.fallback || null };
}

/** Count pending inbox messages, splitting by workspace scope (protocol v1.2). */
function countInbox(agentId, cwd) {
  const dir = path.join(AB_HOME, 'inboxes', agentId);
  let entries;
  try {
    entries = fs.readdirSync(dir).filter((f) => f.endsWith('.json'));
  } catch {
    return null;
  }
  let pending = 0;
  let elsewhere = 0;
  const from = new Set();
  for (const file of entries) {
    const msg = readJsonFile(path.join(dir, file));
    if (!msg) continue;
    const project = msg.context?.project_path || msg.payload?.project_path || '';
    if (project && cwd && project !== cwd) {
      elsewhere++;
      continue;
    }
    pending++;
    if (typeof msg.from === 'string') from.add(msg.from);
  }
  return { pending, elsewhere, from: Array.from(from) };
}

/**
 * Reduce a tool target to something safe to transmit: a path relative to cwd,
 * or null. Absolute paths outside cwd are reduced to their basename so a card
 * can still say what was touched without disclosing the tree layout.
 */
function safeTarget(toolName, input, cwd) {
  if (!input || OPAQUE_TOOLS.has(toolName)) return null;
  const raw =
    typeof input === 'string'
      ? input
      : input.file_path || input.path || input.notebook_path || input.filePath || null;
  if (typeof raw !== 'string' || !raw) return null;
  if (!path.isAbsolute(raw)) return raw;
  if (cwd && (raw === cwd || raw.startsWith(cwd + '/'))) {
    return path.relative(cwd, raw) || path.basename(raw);
  }
  return path.basename(raw);
}

function buildEvent(hook) {
  const eventName = hook.hook_event_name;
  if (!FORWARDED.has(eventName)) return null;

  const terminalId = process.env.ONDA_TERMINAL_ID;
  if (!terminalId) return null;

  const cwd = hook.cwd || process.env.CLAUDE_PROJECT_DIR || process.cwd();
  const { map, id: agentId } = detectAgent(cwd);

  const event = {
    v: 1,
    event: eventName,
    ts: new Date().toISOString(),
    session: {
      id: hook.session_id || `pid-${process.ppid}`,
      agentBin: process.env.CLAUDE_AGENT_BIN || 'claude',
      cwd,
    },
    onda: { terminalId },
  };

  if (process.env.ONDA_PANE_ID) event.onda.paneId = process.env.ONDA_PANE_ID;
  if (process.env.ONDA_TAB_ID) event.onda.tabId = process.env.ONDA_TAB_ID;
  if (process.env.ONDA_WORKSPACE_ID) event.onda.workspaceId = process.env.ONDA_WORKSPACE_ID;

  if (agentId) {
    const entry = map?.agents?.[agentId] || {};
    event.agent = { id: agentId };
    if (entry.display_name) event.agent.display_name = entry.display_name;
    if (entry.domain) event.agent.domain = entry.domain;
    if (typeof entry.is_god === 'boolean') event.agent.is_god = entry.is_god;
  }

  if (eventName === 'PreToolUse' || eventName === 'PostToolUse') {
    const name = hook.tool_name || hook.tool?.name;
    if (name) {
      event.tool = { name, target: safeTarget(name, hook.tool_input, cwd) };
      if (hook.subagent_type) event.tool.subagent = hook.subagent_type;
      if (eventName === 'PostToolUse') {
        const errored =
          hook.tool_response?.is_error === true ||
          hook.tool_error != null ||
          hook.error != null;
        event.tool.ok = !errored;
      }
    }
  }

  if (agentId && (eventName === 'SessionStart' || eventName === 'UserPromptSubmit' || eventName === 'Stop')) {
    const inbox = countInbox(agentId, cwd);
    if (inbox) event.inbox = inbox;
  }

  return event;
}

/**
 * Write one notification and give up fast. Resolves either way: the caller has
 * nothing to decide, since a failed send must look exactly like a success.
 */
function sendEvent(socketPath, event) {
  return new Promise((resolve) => {
    let settled = false;
    const done = () => {
      if (settled) return;
      settled = true;
      try {
        socket.destroy();
      } catch {
        /* already gone */
      }
      resolve();
    };

    const timer = setTimeout(done, WRITE_TIMEOUT_MS);
    timer.unref?.();

    const socket = net.createConnection(socketPath);
    socket.setTimeout(WRITE_TIMEOUT_MS);
    socket.on('error', done);
    socket.on('timeout', done);
    socket.on('connect', () => {
      const payload = JSON.stringify({ jsonrpc: '2.0', method: 'agent.event', params: event });
      socket.write(payload + '\n', () => {
        clearTimeout(timer);
        done();
      });
    });
  });
}

async function main() {
  const socketPath = process.env.ONDA_SOCKET;
  // No driver: nothing to report. Other Agent Team OS hooks still run.
  if (!socketPath) return;

  const raw = await readStdin();
  let hook;
  try {
    hook = JSON.parse(raw);
  } catch {
    return;
  }
  if (!hook || typeof hook !== 'object') return;

  const event = buildEvent(hook);
  if (!event) return;

  await sendEvent(socketPath, event);
}

// Invariant 1: exit 0 no matter what happened.
main()
  .catch(() => {})
  .finally(() => process.exit(0));

export { buildEvent, detectAgent, safeTarget, countInbox };
