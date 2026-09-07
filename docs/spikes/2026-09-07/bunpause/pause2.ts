// 실제 pane 모양: 셸이 뜨고, 사용자가 그 안에서 프로그램을 돌린다.
// 출력을 내는 것은 셸이 아니라 그 자식이다. SIGSTOP 을 셸에만 보내면 멈추는가?
let bytes = 0;
let term: any = null;
const proc = Bun.spawn(["/bin/sh", "-i"], {
  terminal: { cols: 100, rows: 30, data(t: any, c: Uint8Array) { term = t; bytes += c.length; }, exit() {} },
});
const cpu = (pid: number) => {
  const o = Bun.spawnSync(["ps", "-p", String(pid), "-o", "%cpu="]).stdout.toString().trim();
  return parseFloat(o || "0");
};
const sample = async (label: string, ms: number) => {
  const b0 = bytes; await Bun.sleep(ms);
  const mb = (bytes - b0) / 1048576;
  console.log(`  ${label.padEnd(34)} 받은 ${mb.toFixed(1).padStart(6)}MB   셸 CPU ${cpu(proc.pid).toFixed(0).padStart(3)}%`);
};

await Bun.sleep(500);
// 셸 안에서 자식을 띄운다 — 이게 실제 사용 모습이다
console.log("  터미널 객체:", term ? Object.getOwnPropertyNames(Object.getPrototypeOf(term)).join(",") : "아직 없음");
term.write('sh -c \'i=0; while :; do i=$((i+1)); echo "SEQ $i 0123456789abcdef"; done\'\n');
await Bun.sleep(800);

console.log("── 셸 안에서 자식이 출력할 때 SIGSTOP 이 먹는가 ──");
await sample("1. 그대로", 1500);

process.kill(proc.pid, "SIGSTOP");
await sample("2. 셸에만 SIGSTOP", 1500);
process.kill(proc.pid, "SIGCONT");

// 프로세스 그룹이 나와 같으면 -pid 로 보내면 나도 얼어붙는다. 먼저 확인한다.
const pgid = Bun.spawnSync(["ps","-o","pgid=","-p",String(proc.pid)]).stdout.toString().trim();
const mine = Bun.spawnSync(["ps","-o","pgid=","-p",String(process.pid)]).stdout.toString().trim();
console.log(`  자식 pgid=${pgid} / 내 pgid=${mine} → ${pgid===mine ? "같다! -pid 는 나도 얼린다" : "다르다, 안전"}`);
if (pgid !== mine) {
  process.kill(-Number(pgid), "SIGSTOP");
  await sample("3. 프로세스 그룹에 SIGSTOP", 1500);
  process.kill(-Number(pgid), "SIGCONT");
}

proc.kill();
