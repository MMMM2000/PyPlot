import { useEffect, useRef, useState } from 'react';
import {
  Serialport,
  SerialportInfo,
} from '@kuyoonjo/tauri-plugin-serialport-api';
import { open } from '@tauri-apps/api/dialog';
import { writeTextFile, createDir } from '@tauri-apps/api/fs';
import { join, homeDir } from '@tauri-apps/api/path';
import FileNameBuilder from './FileNameBuilder';

const DEFAULT_CMD = '>2050;1270;1;';
const DEFAULT_SAMPLE_COUNT = 2000;
const DEFAULT_LOG_FILE_NAME = 'log';
const BAUD_RATES = [921600, 460800, 115200, 57600, 19200, 9600];

export default function App() {
  const [ports, setPorts] = useState<SerialportInfo[]>([]);
  const [path, setPath] = useState('');
  const [baud, setBaud] = useState(BAUD_RATES[0]);
  const [port, setPort] = useState<Serialport | null>(null);
  const [connected, setConnected] = useState(false);
  const [command, setCommand] = useState(DEFAULT_CMD);
  const [response, setResponse] = useState('');
  const bufferRef = useRef('');
  const [logging, setLogging] = useState(false);
  const [sampleCount, setSampleCount] = useState(DEFAULT_SAMPLE_COUNT);
  const [sampleIdx, setSampleIdx] = useState(0);
  const logFileRef = useRef('');
  const lastTimeRef = useRef<number | null>(null);
  const rateWindowRef = useRef<number[]>([]);
  const [sampleRate, setSampleRate] = useState<number | null>(null);
  const [logDir, setLogDir] = useState('');
  const [fileName, setFileName] = useState(DEFAULT_LOG_FILE_NAME);
  const [useSubdir, setUseSubdir] = useState(false);

  useEffect(() => {
    refreshPorts();
    (async () => {
      const home = await homeDir();
      const dir = await join(home, 'python_plot_logs');
      await createDir(dir, { recursive: true });
      setLogDir(dir);
    })();
  }, []);

  async function refreshPorts() {
    const p = await Serialport.available_ports();
    setPorts(p);
    if (p.length > 0) {
      setPath(p[0].path);
    }
  }

  async function toggleConnection() {
    if (connected) {
      await port?.close();
      setConnected(false);
      setPort(null);
      return;
    }
    const p = new Serialport({ path, baudRate: baud, size: 1 });
    await p.open();
    await p.listen(onData, true);
    setPort(p);
    setConnected(true);
  }

  async function chooseLogDir() {
    const selected = await open({ directory: true });
    if (typeof selected === 'string') {
      await createDir(selected, { recursive: true });
      setLogDir(selected);
    }
  }

  async function onData(data: string) {
    bufferRef.current += data;
    const parts = bufferRef.current.split(/\r?\n/);
    bufferRef.current = parts.pop() ?? '';
    for (const line of parts) {
      setResponse(line);
      const now = performance.now() / 1000;
      if (lastTimeRef.current !== null) {
        const dt = now - lastTimeRef.current;
        if (dt > 0) {
          const rate = 1 / dt;
          const arr = rateWindowRef.current;
          arr.push(rate);
          if (arr.length > 1000) arr.shift();
          const avg = arr.reduce((a, b) => a + b, 0) / arr.length;
          setSampleRate(avg);
        }
      }
      lastTimeRef.current = now;
      if (logging) {
        await writeTextFile(logFileRef.current, line.replace(/^>/, '') + '\n', {
          append: true,
        });
        setSampleIdx((idx) => idx + 1);
      }
    }
  }

  useEffect(() => {
    if (logging && sampleRate) {
      if (sampleIdx >= sampleCount) {
        setLogging(false);
      }
    }
  }, [sampleIdx, sampleCount, logging, sampleRate]);

  async function sendCommand() {
    if (port) {
      await port.write(command + '\n');
    }
  }

  async function startLogging() {
    let dir = logDir;
    if (!dir) {
      const home = await homeDir();
      dir = await join(home, 'python_plot_logs');
    }
    let base = fileName || DEFAULT_LOG_FILE_NAME;
    let fullDir = dir;
    if (useSubdir) {
      const parts = base.split(' ');
      if (parts.length > 1) {
        const folder = parts.slice(0, -1).join(' ');
        fullDir = await join(dir, folder);
      }
    }
    await createDir(fullDir, { recursive: true });
    const fullPath = await join(fullDir, `${base}.txt`);
    logFileRef.current = fullPath;
    await writeTextFile(fullPath, '');
    setSampleIdx(0);
    setLogging(true);
  }

  function cancelLogging() {
    setLogging(false);
  }

  const effectiveRemaining = logging ? sampleCount - sampleIdx : sampleCount;
  const timeRemaining = sampleRate ? Math.ceil(effectiveRemaining / sampleRate) : null;
  const progress = Math.floor((sampleIdx / sampleCount) * 100);

  return (
    <div className="min-h-screen p-4 space-y-4 bg-white text-gray-900 dark:bg-gray-900 dark:text-gray-100">
      <h1 className="text-2xl font-bold">Data Logger</h1>
      <div className="flex items-center space-x-2">
        <select
          value={path}
          onChange={(e) => setPath(e.target.value)}
          className="border p-1"
        >
          {ports.map((p) => (
            <option key={p.path} value={p.path}>
              {p.path}
            </option>
          ))}
        </select>
        <button
          onClick={refreshPorts}
          className="bg-gray-500 text-white px-2 py-1 rounded"
        >
          Refresh
        </button>
        <select
          value={baud}
          onChange={(e) => setBaud(parseInt(e.target.value))}
          className="border p-1"
        >
          {BAUD_RATES.map((b) => (
            <option key={b} value={b}>
              {b}
            </option>
          ))}
        </select>
        <button
          onClick={toggleConnection}
          className="bg-blue-500 text-white px-2 py-1 rounded"
        >
          {connected ? 'Disconnect' : 'Connect'}
        </button>
      </div>

      <div>
        <div className="mb-2">Response: {response}</div>
        <input
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          className="border p-1 w-full"
        />
        <button
          onClick={sendCommand}
          disabled={!connected}
          className="mt-2 bg-green-500 text-white px-2 py-1 rounded"
        >
          Send
        </button>
      </div>

      <div className="space-y-2">
        <div className="flex items-center space-x-2">
          <input
            value={logDir}
            onChange={(e) => setLogDir(e.target.value)}
            className="border p-1 flex-grow"
          />
          <button
            onClick={chooseLogDir}
            className="bg-gray-500 text-white px-2 py-1 rounded"
          >
            Browse
          </button>
        </div>
        <FileNameBuilder fileName={fileName} setFileName={setFileName} />
        <label className="flex items-center space-x-1">
          <input
            type="checkbox"
            checked={useSubdir}
            onChange={(e) => setUseSubdir(e.target.checked)}
          />
          <span>Subfolder</span>
        </label>
        <div className="flex items-center space-x-2">
          <input
            type="number"
            value={sampleCount}
            onChange={(e) => setSampleCount(parseInt(e.target.value))}
            className="border p-1 w-32"
          />
          <button
            onClick={startLogging}
            disabled={!connected || logging}
            className="bg-purple-500 text-white px-2 py-1 rounded"
          >
            Record
          </button>
          <button
            onClick={cancelLogging}
            disabled={!logging}
            className="bg-gray-400 text-white px-2 py-1 rounded"
          >
            Cancel
          </button>
        </div>
        <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded">
          <div
            className="h-full bg-purple-500 rounded"
            style={{ width: `${progress}%` }}
          ></div>
        </div>
        <div>Sample rate: {sampleRate ? sampleRate.toFixed(1) : 'N/A'} Hz</div>
        <div>Time remaining: {timeRemaining ?? 'N/A'}s</div>
      </div>
    </div>
  );
}

