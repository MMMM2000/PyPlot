import { useState } from 'react';
import { invoke } from '@tauri-apps/api/tauri';

interface LogEntry {
  timestamp: string;
  value: number;
}

export default function App() {
  const [logs, setLogs] = useState<LogEntry[]>([]);

  async function loadLogs() {
    const data = await invoke<LogEntry[]>('get_sample_logs');
    setLogs(data);
  }

  return (
    <div className="p-4">
      <h1 className="text-2xl font-bold mb-4">Data Logger</h1>
      <button onClick={loadLogs} className="bg-blue-500 text-white px-4 py-2 rounded">
        Load Sample Logs
      </button>
      <ul className="mt-4 space-y-2">
        {logs.map((log, idx) => (
          <li key={idx} className="border rounded p-2 flex justify-between">
            <span>{log.timestamp}</span>
            <span>{log.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
