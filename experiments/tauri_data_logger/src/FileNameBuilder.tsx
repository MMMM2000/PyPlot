import { useState, useEffect } from 'react';

interface Props {
  fileName: string;
  setFileName: (name: string) => void;
}

export default function FileNameBuilder({ fileName, setFileName }: Props) {
  const [format, setFormat] = useState<'Stress' | 'Temperature' | 'Maxion' | 'Custom'>('Stress');

  // Stress fields
  const [sComp, setSComp] = useState('FeSiBP');
  const [sSample, setSSample] = useState('156_2');
  const [sNumber, setSNumber] = useState('s2-1');
  const [sEnd, setSEnd] = useState('a');
  const [sAnneal, setSAnneal] = useState('74mA');
  const [sLoad, setSLoad] = useState(2.5);
  const [sDir, setSDir] = useState('a');

  // Temperature fields
  const [tComp, setTComp] = useState('FeSiBP');
  const [tSample, setTSample] = useState('156_2');
  const [tNumber, setTNumber] = useState('s2-1');
  const [tAnneal, setTAnneal] = useState('74mA');
  const [tTemp, setTTemp] = useState('25C');

  // Maxion fields
  const [mHead, setMHead] = useState(1);
  const [mDesc, setMDesc] = useState('');
  const [mCoils, setMCoils] = useState('2');

  useEffect(() => {
    if (format === 'Stress') {
      const loadStr = Number.isInteger(sLoad)
        ? `${sLoad}`
        : `${sLoad}`.replace('.', ',');
      setFileName(
        `${sComp} ${sSample} ${sNumber}${sEnd} ${sAnneal} ${loadStr}${sDir}`,
      );
    } else if (format === 'Temperature') {
      setFileName(`${tComp} ${tSample} ${tNumber} ${tAnneal} ${tTemp}`);
    } else if (format === 'Maxion') {
      setFileName(`${mHead} ${mDesc} ${mCoils} coils`);
    }
  }, [
    format,
    sComp,
    sSample,
    sNumber,
    sEnd,
    sAnneal,
    sLoad,
    sDir,
    tComp,
    tSample,
    tNumber,
    tAnneal,
    tTemp,
    mHead,
    mDesc,
    mCoils,
    setFileName,
  ]);

  const readOnly = format !== 'Custom';

  return (
    <div className="space-y-2">
      <div className="flex items-center space-x-2">
        <input
          value={fileName}
          onChange={(e) => setFileName(e.target.value)}
          readOnly={readOnly}
          className="border p-1 flex-grow"
        />
        <select
          value={format}
          onChange={(e) => setFormat(e.target.value as any)}
          className="border p-1"
        >
          <option value="Stress">Stress</option>
          <option value="Temperature">Temperature</option>
          <option value="Maxion">Maxion</option>
          <option value="Custom">Custom</option>
        </select>
      </div>

      {format === 'Stress' && (
        <div className="grid grid-cols-2 gap-2">
          <input
            placeholder="Composition"
            value={sComp}
            onChange={(e) => setSComp(e.target.value)}
            className="border p-1"
          />
          <input
            placeholder="Microwire"
            value={sSample}
            onChange={(e) => setSSample(e.target.value)}
            className="border p-1"
          />
          <input
            placeholder="Sample number"
            value={sNumber}
            onChange={(e) => setSNumber(e.target.value)}
            className="border p-1"
          />
          <select
            value={sEnd}
            onChange={(e) => setSEnd(e.target.value)}
            className="border p-1"
          >
            <option value="a">Marked end (a)</option>
            <option value="b">Unmarked end (b)</option>
          </select>
          <input
            placeholder="Annealing"
            value={sAnneal}
            onChange={(e) => setSAnneal(e.target.value)}
            className="border p-1"
          />
          <input
            type="number"
            placeholder="Load"
            value={sLoad}
            onChange={(e) => setSLoad(parseFloat(e.target.value))}
            className="border p-1"
          />
          <select
            value={sDir}
            onChange={(e) => setSDir(e.target.value)}
            className="border p-1"
          >
            <option value="a">Loading (a)</option>
            <option value="b">Unloading (b)</option>
          </select>
        </div>
      )}

      {format === 'Temperature' && (
        <div className="grid grid-cols-2 gap-2">
          <input
            placeholder="Composition"
            value={tComp}
            onChange={(e) => setTComp(e.target.value)}
            className="border p-1"
          />
          <input
            placeholder="Microwire"
            value={tSample}
            onChange={(e) => setTSample(e.target.value)}
            className="border p-1"
          />
          <input
            placeholder="Sample number"
            value={tNumber}
            onChange={(e) => setTNumber(e.target.value)}
            className="border p-1"
          />
          <input
            placeholder="Annealing"
            value={tAnneal}
            onChange={(e) => setTAnneal(e.target.value)}
            className="border p-1"
          />
          <select
            value={tTemp}
            onChange={(e) => setTTemp(e.target.value)}
            className="border p-1"
          >
            <option value="25C">25C</option>
            <option value="25-100C">25-100C</option>
            <option value="100C">100C</option>
          </select>
        </div>
      )}

      {format === 'Maxion' && (
        <div className="grid grid-cols-2 gap-2">
          <input
            type="number"
            min={1}
            max={6}
            placeholder="Head"
            value={mHead}
            onChange={(e) => setMHead(parseInt(e.target.value))}
            className="border p-1"
          />
          <input
            placeholder="Description"
            value={mDesc}
            onChange={(e) => setMDesc(e.target.value)}
            className="border p-1"
          />
          <select
            value={mCoils}
            onChange={(e) => setMCoils(e.target.value)}
            className="border p-1"
          >
            <option value="2">2</option>
            <option value="3">3</option>
          </select>
        </div>
      )}
    </div>
  );
}

