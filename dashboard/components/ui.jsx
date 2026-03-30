"use client";

import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export function PageHeader({ eyebrow, title, description, action }) {
  return (
    <div className="rounded-[28px] border border-white/60 bg-white/75 p-6 shadow-panel backdrop-blur">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="text-xs uppercase tracking-[0.28em] text-ink/45">{eyebrow}</div>
          <h2 className="mt-2 text-3xl font-semibold tracking-tight">{title}</h2>
          <p className="mt-2 max-w-2xl text-sm text-ink/62">{description}</p>
        </div>
        {action}
      </div>
    </div>
  );
}

export function StatCard({ label, value, hint }) {
  return (
    <div className="rounded-[24px] border border-white/70 bg-white/80 p-5 shadow-panel">
      <div className="text-xs uppercase tracking-[0.24em] text-ink/45">{label}</div>
      <div className="mt-3 text-3xl font-semibold">{value}</div>
      {hint ? <div className="mt-2 text-sm text-ink/58">{hint}</div> : null}
    </div>
  );
}

export function Panel({ title, subtitle, children }) {
  return (
    <section className="rounded-[28px] border border-white/70 bg-white/80 p-6 shadow-panel">
      {(title || subtitle) && (
        <header className="mb-5">
          {title ? <h3 className="text-lg font-semibold">{title}</h3> : null}
          {subtitle ? <p className="mt-1 text-sm text-ink/58">{subtitle}</p> : null}
        </header>
      )}
      {children}
    </section>
  );
}

export function DataTable({ columns, rows }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-sm">
        <thead>
          <tr className="border-b border-ink/10 text-ink/50">
            {columns.map((column) => (
              <th key={column.key} className="px-3 py-3 font-medium">
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={row.id || index} className="border-b border-ink/5 last:border-b-0">
              {columns.map((column) => (
                <td key={column.key} className="px-3 py-3 text-ink/82">
                  {column.render ? column.render(row) : row[column.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TrendChart({ data }) {
  return (
    <div className="h-72">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#d8dedb" />
          <XAxis dataKey="date" stroke="#60717f" />
          <YAxis stroke="#60717f" />
          <Tooltip />
          <Line type="monotone" dataKey="revenue" stroke="#af4d1f" strokeWidth={3} dot={false} />
          <Line type="monotone" dataKey="orders" stroke="#1d4f46" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function ProductChart({ data }) {
  return (
    <div className="h-72">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#d8dedb" />
          <XAxis dataKey="name" stroke="#60717f" />
          <YAxis stroke="#60717f" />
          <Tooltip />
          <Bar dataKey="quantity" fill="#29334d" radius={[8, 8, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
