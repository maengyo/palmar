// Bun 이 흐름 제어를 하려면 PTY 의 포그라운드 프로세스 그룹을 알아야 한다(위 fg.py 참고).
// Bun.Terminal 에는 그런 API 가 없다. libc 의 tcgetpgrp 를 FFI 로 부를 수 있는가?
import { dlopen, FFIType, suffix } from "bun:ffi";
let tcgetpgrp: any = null, killpg: any = null;
try {
  const lib = dlopen(`libc.${suffix}`, {
    tcgetpgrp: { args: [FFIType.i32], returns: FFIType.i32 },
    killpg:    { args: [FFIType.i32, FFIType.i32], returns: FFIType.i32 },
  });
  tcgetpgrp = lib.symbols.tcgetpgrp; killpg = lib.symbols.killpg;
  console.log("  dlopen(libc) 성공 — tcgetpgrp/killpg 를 부를 수 있다");
} catch (e) { console.log("  dlopen 실패:", String(e).slice(0, 90)); }

if (tcgetpgrp) {
  let bytes = 0, term: any = null;
  const proc = Bun.spawn(["/bin/sh", "-i"], {
    terminal: { cols: 100, rows: 30, data(t: any, c: Uint8Array) { term = t; bytes += c.length; }, exit() {} },
  });
  await Bun.sleep(600);
  term.write('sh -c \'i=0; while :; do i=$((i+1)); echo "SEQ $i 0123456789abcdef"; done\'\n');
  await Bun.sleep(1000);

  // Bun.Terminal 이 PTY master fd 를 내주는가? — 이게 없으면 tcgetpgrp 를 못 쓴다
  const keys = Object.getOwnPropertyNames(Object.getPrototypeOf(term));
  const hasFd = keys.filter(k => /fd|handle|fileno/i.test(k));
  console.log("  터미널이 내주는 것:", keys.join(","));
  console.log("  fd 로 보이는 것:", hasFd.length ? hasFd.join(",") : "없다 ← PTY master fd 를 못 얻는다");

  const sample = async (l: string, ms: number) => {
    const b0 = bytes; await Bun.sleep(ms);
    console.log(`  ${l.padEnd(30)} 받은 ${((bytes-b0)/1048576).toFixed(1).padStart(6)}MB`);
  };
  await sample("그대로", 1200);
  proc.kill(9);
}
