#!/usr/bin/env python3
"""
Servidor em segundo plano: sem janela para alguem fechar, e de pe' sozinho.

- o --vigia levanta o servidor de novo quando ele morre, e espera cada vez
  mais quando ele cai logo depois de subir (sem encher o log);
- parar o vigia para o servidor junto, senao a porta ficaria presa;
- abrir o .exe de novo com o servidor ja' rodando explica o que houve, em vez
  de um traceback;
- o instalar_servico.bat nao cai nas armadilhas da tarefa agendada.
"""

import http.client
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from teste_servidor import banco
from teste_cliente_ptx import porta_livre

SERVIDOR = os.path.join(RAIZ, "servidor", "servidor_rotas.py")


def saude(porta):
    try:
        c = http.client.HTTPConnection("127.0.0.1", porta, timeout=3)
        c.request("GET", "/api/saude")
        r = c.getresponse(); r.read(); c.close()
        return r.status == 200
    except Exception:
        return False


def espera(cond, limite=40):
    fim = time.time() + limite
    while time.time() < fim:
        if cond():
            return True
        time.sleep(0.3)
    return False


def le(caminho):
    try:
        with open(caminho, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


class TesteVigia(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="segundo-plano-")
        self.log = os.path.join(self.dir, "servidor.log")
        self.porta = porta_livre()
        self.proc = None

    def tearDown(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=15)
            except Exception:
                self.proc.kill()
        for pid in self.pids():                 # nada de servidor orfao
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def pids(self):
        return [int(p) for p in re.findall(r"servidor iniciado \(processo (\d+)\)",
                                           le(self.log))]

    def vigia(self, *extra, db=None):
        self.proc = subprocess.Popen(
            [sys.executable, SERVIDOR, "--vigia", "--sem-console",
             "--log", self.log, "--db", db or banco(), "--porta", str(self.porta),
             "--endereco", "127.0.0.1", "--intervalo", "3600",
             "--ortofotos", "", "--desmontes", os.path.join(self.dir, "d.json")]
            + list(extra),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_servidor_que_morre_volta_sozinho(self):
        self.vigia()
        self.assertTrue(espera(lambda: saude(self.porta)), "o servidor nao subiu")
        primeiro = self.pids()[-1]
        os.kill(primeiro, signal.SIGKILL)       # queda de verdade, sem aviso
        self.assertTrue(espera(lambda: len(self.pids()) >= 2 and saude(self.porta)),
                        "o servidor morreu e nao voltou:\n" + le(self.log))
        self.assertNotEqual(self.pids()[-1], primeiro)
        self.assertIn("o servidor parou", le(self.log))

    def test_parar_o_vigia_para_o_servidor(self):
        self.vigia()
        self.assertTrue(espera(lambda: saude(self.porta)))
        self.proc.send_signal(signal.SIGTERM)
        self.proc.wait(timeout=15)
        self.assertTrue(espera(lambda: not saude(self.porta), 15),
                        "o vigia saiu e deixou o servidor orfao, prendendo a porta")

    def test_queda_em_seguida_espera_cada_vez_mais(self):
        """Banco sumido: o servidor sai na hora. Sem espera crescente seriam
           doze reinicios por minuto, o dia inteiro, enchendo o log."""
        self.vigia(db=os.path.join(self.dir, "nao_existe.db"))
        self.assertTrue(espera(lambda: "em 10s" in le(self.log), 30),
                        "nao dobrou a espera:\n" + le(self.log))
        texto = le(self.log)
        self.assertLess(texto.index("em 5s"), texto.index("em 10s"))
        self.assertEqual(texto.count("servidor iniciado"), 2,
                         "reiniciou mais vezes do que devia nesse tempo")


class TestePortaOcupada(unittest.TestCase):

    def test_segunda_copia_explica_em_vez_de_estourar(self):
        porta = porta_livre()
        d = tempfile.mkdtemp(prefix="porta-")
        comum = ["--db", banco(), "--porta", str(porta), "--endereco", "127.0.0.1",
                 "--intervalo", "3600", "--ortofotos", "",
                 "--desmontes", os.path.join(d, "d.json")]
        primeiro = subprocess.Popen([sys.executable, SERVIDOR] + comum,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.assertTrue(espera(lambda: saude(porta)))
            r = subprocess.run([sys.executable, SERVIDOR] + comum, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 1)
            self.assertIn("ocupada", r.stdout)
            self.assertIn("segundo plano", r.stdout)
            self.assertNotIn("Traceback", r.stdout + r.stderr)
        finally:
            primeiro.terminate(); primeiro.wait(timeout=10)
            import shutil
            shutil.rmtree(d, ignore_errors=True)


class TesteInstalarServico(unittest.TestCase):
    """O .bat so' roda no Windows; aqui se confere o que ja' deu errado."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(RAIZ, "servidor", "instalar_servico.bat"),
                  encoding="utf-8") as f:
            cls.bat = f.read()

    def test_a_tarefa_roda_com_o_vigia_e_sem_console(self):
        self.assertRegex(self.bat, r"set ARGS=--vigia --sem-console")

    def test_tira_o_limite_de_tres_dias(self):
        """Tarefa criada pelo schtasks e' morta depois de 72 h rodando."""
        self.assertIn("-ExecutionTimeLimit ([TimeSpan]::Zero)", self.bat)
        self.assertIn("-DontStopIfGoingOnBatteries", self.bat)

    def test_variavel_nao_e_lida_dentro_do_bloco_em_que_muda(self):
        """Dentro de ( ), o cmd troca %X% pelo valor de ANTES do bloco. Foi
           assim que o --db sumia quando havia rajant.json."""
        profundidade, mudadas = 0, set()
        for n, linha in enumerate(self.bat.splitlines(), 1):
            t = linha.strip()
            if t.upper().startswith("REM"):
                continue
            if profundidade:
                for v in re.findall(r"%(\w+)%", t):
                    self.assertNotIn(v.upper(), mudadas,
                                     f"linha {n}: %{v}% lido no bloco onde muda")
                m = re.match(r'set\s+"?(\w+)=', t, re.I)
                if m:
                    mudadas.add(m.group(1).upper())
            profundidade += t.count("(") - t.count(")") if not t.startswith("echo") else 0
            if profundidade <= 0:
                profundidade, mudadas = 0, set()

    def test_parar_derruba_os_dois_processos(self):
        """Com o vigia, matar so' o servidor nao adianta: ele volta."""
        parar = self.bat[self.bat.index(":parar"):self.bat.index(":iniciar")]
        self.assertIn("schtasks /End", parar)
        self.assertIn("taskkill /F /T /IM servidor_rotas.exe", parar)


if __name__ == "__main__":
    unittest.main(verbosity=2)
