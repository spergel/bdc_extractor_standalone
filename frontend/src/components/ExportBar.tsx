import { saveAs } from 'file-saver';
import Papa from 'papaparse';
import type { Holding } from '../data/adapter';

type Props = { data: Holding[]; filename?: string };

export function ExportBar({ data, filename = 'OFS_holdings' }: Props) {
  const dlJson = () => {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    saveAs(blob, `${filename}.json`);
  };
  const dlCsv = () => {
    const csv = Papa.unparse(data as any);
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    saveAs(blob, `${filename}.csv`);
  };
  return (
    <div className="flex gap-2 p-2 border-t border-[#c0c0c0] bg-[#c0c0c0]">
      <button className="btn" onClick={dlCsv}>Export CSV</button>
      <button className="btn" onClick={dlJson}>Export JSON</button>
    </div>
  );
}






