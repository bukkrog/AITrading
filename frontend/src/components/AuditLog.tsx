import type { AuditEntry } from "../types";

export function AuditLog({ audit }: { audit: AuditEntry[] }) {
  return (
    <div className="card">
      <h2>Audit log</h2>
      <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
        Every signal, decision, order, fill and control action is recorded here (principle #7).
      </p>
      {audit.length > 0 ? (
        <div style={{ overflowX: "auto" }}>
          <table>
            <thead>
              <tr><th>Time</th><th>Category</th><th>Action</th><th>Symbol</th><th>Message</th></tr>
            </thead>
            <tbody>
              {audit.map((e) => (
                <tr key={e.id}>
                  <td className="muted">{new Date(e.ts).toLocaleString()}</td>
                  <td>{e.category}</td>
                  <td>{e.action}</td>
                  <td>{e.symbol ?? ""}</td>
                  <td className="audit-msg" style={{ textAlign: "left" }}>{e.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">No audit entries yet.</p>
      )}
    </div>
  );
}
