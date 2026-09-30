import subprocess, shutil, sys, pathlib
R = pathlib.Path(sys.argv[1]); D = R/"factcheck_mvp"/"decisao.py"; SE = R/"factcheck_mvp"/"selos.py"
orig_d = D.read_text(); orig_s = SE.read_text()
M = {
 "M1 inverte sinal REFUTA/SUSTENTA": (D, 'd = 1.0 if it.classe == "REFUTA" else -1.0', 'd = -1.0 if it.classe == "REFUTA" else 1.0'),
 "M2 TAU ln3 -> ln1.5": (D, "TAU = math.log(3)", "TAU = math.log(1.5)"),
 "M2b TAU ln3 -> ln5": (D, "TAU = math.log(3)", "TAU = math.log(5)"),
 "M3 F_NAO_CURADA 0.6 -> 1.0": (D, "F_NAO_CURADA = 0.6", "F_NAO_CURADA = 1.0"),
 "M4 W_VEREDITO 1.5 -> 1.0": (D, "W_VEREDITO = 1.5", "W_VEREDITO = 1.0"),
 "M5 RELATA nao aplica selo": (D, 'CLASSES_TRATA = ("SUSTENTA", "REFUTA", "RELATA_SEM_ENDOSSO")', 'CLASSES_TRATA = ("SUSTENTA", "REFUTA")'),
 "M6 combinacao: sempre max|L|": (D, "L = mx if mx >= TAU else max(por_af.values(), key=abs)", "L = max(por_af.values(), key=abs)"),
 "M6b combinacao: soma das afirmacoes": (D, "L = mx if mx >= TAU else max(por_af.values(), key=abs)", "L = sum(por_af.values())"),
 "M7 cluster soma (sem 1 voto)": (D, "peso = max(abs(v) for v, _, _ in contribs if v * sinal > 0)", "peso = abs(soma)"),
 "M8 ignora polaridade": (D, 's = -1.0 if af.polaridade == "nega" else 1.0', "s = 1.0"),
 "M9 conflito soma em vez de anular": (D, "if post[0] * ver[0] < 0:", "if False:"),
 "M10 F_SO_TITULO 0.7 -> 1.0": (D, "F_SO_TITULO = 0.7", "F_SO_TITULO = 1.0"),
 "M11 postura+selo mesma pagina somam": (D, "return [melhor]", "return [post, ver]"),
 "M12 ENGANOSO 0.7 -> 1.0": (SE, '"ENGANOSO": 0.7', '"ENGANOSO": 1.0'),
 "M13 SEM_EVIDENCIA 0.4 -> 0.0": (SE, '"SEM_EVIDENCIA": 0.4', '"SEM_EVIDENCIA": 0.0'),
 "M14 remove trava vago": (D, "if ev.vago:", "if False:"),
 "M15 remove trava opiniao": (D, "elif ev.opiniao and not dec.vereditos_aplicados:", "elif False:"),
 "M16 fallback conta (motor ignorado)": (D, 'if (it.motor or "").startswith("fallback") or it.classe is None:', 'if it.classe is None:'),
}
for nome, (arq, a, b) in M.items():
    txt = arq.read_text(); assert a in txt, nome
    arq.write_text(txt.replace(a, b, 1))
    r = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-x", "--no-header", "-p", "no:cacheprovider"], cwd=R, capture_output=True, text=True)
    last = [l for l in r.stdout.splitlines() if l.strip()][-1]
    falhou = [l for l in r.stdout.splitlines() if l.startswith("FAILED")]
    print(f"{nome:<40} {'MORTO ' if r.returncode else 'VIVO  '} {last[:60]} {falhou[0][7:90] if falhou else ''}")
    D.write_text(orig_d); SE.write_text(orig_s)
