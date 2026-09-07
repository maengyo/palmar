// 스파이크 I — Bun 이 흐름 제어를 할 수 있는가 (② 의 마지막 미검증 항목)
//
// 스파이크 D 에서 "Bun 은 Bun.Terminal 에 pause 가 없어 못 멈춘다" 고 적었다. 우회로로
// 자식에게 SIGSTOP 을 보내는 길을 적어만 두고 재지 않았다. 그게 되면 파이썬의 우위 하나가 사라진다.
//
// 재는 것: 최대 출력 중에 SIGSTOP → CPU 와 메모리가 실제로 0 으로 가는가, SIGCONT 로 돌아오는가.
const FLOOD = 'i=0; while :; do i=$((i+1)); echo "SEQ $i 0123456789abcdef0123456789abcdef"; done';

let bytes = 0;
let paused = false;

const proc = Bun.spawn(["/bin/sh", "-c", FLOOD], {
  terminal: {
    cols: 100, rows: 30,
    data(_t, chunk: Uint8Array) { bytes += chunk.length; },
    exit() {},
  },
});

const cpu = (pid: number) => {
  const out = Bun.spawnSync(["ps", "-p", String(pid), "-o", "%cpu=,rss="]).stdout.toString().trim();
  const [c, r] = out.split(/\s+/);
  return { cpu: parseFloat(c || "0"), rssMB: parseInt(r || "0") / 1024 };
};

const sample = async (label: string, ms: number) => {
  const b0 = bytes, t0 = Date.now();
  await Bun.sleep(ms);
  const self = cpu(process.pid), child = cpu(proc.pid);
  const mb = (bytes - b0) / 1048576, s = (Date.now() - t0) / 1000;
  console.log(
    `  ${label.padEnd(30)} 받은 ${mb.toFixed(1).padStart(6)}MB (${(mb / s).toFixed(1).padStart(5)}MB/s)` +
    `  bun CPU ${self.cpu.toFixed(0).padStart(3)}% RSS ${self.rssMB.toFixed(0).padStart(3)}MB` +
    `  자식 CPU ${child.cpu.toFixed(0).padStart(3)}%`
  );
};

console.log("── Bun", Bun.version, "SIGSTOP 으로 흐름 제어가 되는가 ──");
await Bun.sleep(600);
await sample("1. 최대 출력, 그대로", 2000);

process.kill(proc.pid, "SIGSTOP"); paused = true;
await sample("2. 자식에게 SIGSTOP", 2000);

process.kill(proc.pid, "SIGCONT"); paused = false;
await sample("3. SIGCONT 로 재개", 2000);

proc.kill();
