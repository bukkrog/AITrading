import { Component, type ErrorInfo, type ReactNode } from "react";

interface State {
  error: Error | null;
}

/** Catches render errors so one bad component/API shape shows a message instead
 *  of blanking the whole page. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("UI error boundary caught:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 24, fontFamily: "system-ui, sans-serif", color: "var(--text, #ddd)" }}>
          <h2>Something went wrong rendering the page</h2>
          <p style={{ color: "var(--muted, #999)" }}>
            The app hit an error but the servers are fine. Try reloading; if it persists, the detail below helps debugging.
          </p>
          <pre style={{
            whiteSpace: "pre-wrap", background: "var(--panel-2, #222)", padding: 12,
            borderRadius: 8, fontSize: 12, overflowX: "auto",
          }}>{String(this.state.error?.message || this.state.error)}</pre>
          <button onClick={() => { this.setState({ error: null }); location.reload(); }}
            style={{ marginTop: 12 }}>Reload</button>
        </div>
      );
    }
    return this.props.children;
  }
}
