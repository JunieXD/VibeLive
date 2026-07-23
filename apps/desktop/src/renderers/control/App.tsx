export function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <h1>ADVX Live</h1>
          <p>AI 虚拟直播间</p>
        </div>
        <span className="status">本地服务待连接</span>
      </header>

      <main>
        <section aria-labelledby="session-heading">
          <h2 id="session-heading">直播会话</h2>
          <dl>
            <div>
              <dt>画面</dt>
              <dd>未选择</dd>
            </div>
            <div>
              <dt>麦克风</dt>
              <dd>未选择</dd>
            </div>
            <div>
              <dt>AI 观众</dt>
              <dd>未配置</dd>
            </div>
          </dl>
          <button type="button" disabled>
            开始
          </button>
        </section>
      </main>
    </div>
  );
}
