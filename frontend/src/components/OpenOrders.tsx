import { useState } from "react";
import { api } from "../api";
import type { OpenOrder } from "../types";

const num = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 2 });

export function OpenOrders({
  orders,
  onChanged,
  onToast,
}: {
  orders: OpenOrder[];
  onChanged: () => void;
  onToast: (m: string) => void;
}) {
  const [busy, setBusy] = useState(false);

  async function act(fn: () => Promise<unknown>, msg: string) {
    setBusy(true);
    try {
      await fn();
      onToast(msg);
      onChanged();
    } catch (e) {
      onToast((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={{ margin: 0 }}>Open orders (pending buy/sell)</h2>
        {orders.length > 0 && (
          <button className="secondary" disabled={busy} onClick={() => act(() => api.cancelSaxoOrder(), "All orders cancelled")}>
            Cancel all
          </button>
        )}
      </div>
      <p className="muted" style={{ fontSize: 12, marginTop: 4 }}>
        Orders the platform has queued at the broker but that haven't filled yet (e.g. market closed).
      </p>
      {orders.length > 0 ? (
        <div style={{ overflowX: "auto" }}>
          <table>
            <thead>
              <tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Type</th><th>Price</th><th>Status</th><th></th></tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.order_id}>
                  <td>{o.symbol}</td>
                  <td className={o.side === "Buy" ? "pos" : "neg"}>{o.side}</td>
                  <td>{num(o.quantity)}</td>
                  <td>{o.order_type}</td>
                  <td>{o.price != null ? num(o.price) : "mkt"}</td>
                  <td>{o.status}</td>
                  <td>
                    <button className="secondary" disabled={busy}
                      onClick={() => act(() => api.cancelSaxoOrder(o.order_id), `Cancelled ${o.symbol}`)}>
                      Cancel
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">No pending orders.</p>
      )}
    </div>
  );
}
