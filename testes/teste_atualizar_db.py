#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Testes da atualizacao automatica do Modular.db a partir dos PTX.

Nao precisa de SSH: o cliente e' injetado. O que importa aqui e' a regra de
seguranca — o banco em producao so' pode ser trocado por um que preste.
"""
import importlib.util, os, shutil, sqlite3, sys, tempfile, threading, time, unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "testes"))
from teste_servidor import banco

_spec = importlib.util.spec_from_file_location(
    "atualizar_db", os.path.join(RAIZ, "servidor", "atualizar_db.py"))
ad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ad)


class ClienteFalso:
    """Serve um arquivo local como se fosse o PTX."""
    def __init__(self, arquivo, mtime, falhar_em=None):
        self.arquivo, self.mtime, self.falhar_em = arquivo, mtime, falhar_em
        self.baixou = 0

    def conectar(self):
        if self.falhar_em == "conectar":
            raise OSError("recusou a conexao")

    def info(self, caminho):
        if self.falhar_em == "info":
            raise OSError("arquivo nao existe")
        return self.mtime, os.path.getsize(self.arquivo)

    def baixar(self, caminho, destino):
        if self.falhar_em == "baixar":
            raise OSError("caiu no meio")
        self.baixou += 1
        shutil.copy2(self.arquivo, destino)

    def fechar(self):
        pass


class TesteFaixaDeEnderecos(unittest.TestCase):
    def test_faixa(self):
        h = ad.expandir_hosts("10.188.98.1-50")
        self.assertEqual(len(h), 50)
        self.assertEqual(h[0], "10.188.98.1")
        self.assertEqual(h[-1], "10.188.98.50")

    def test_mistura_faixa_e_avulso(self):
        h = ad.expandir_hosts("10.188.98.1-3, 10.188.111.254")
        self.assertEqual(h, ["10.188.98.1", "10.188.98.2", "10.188.98.3",
                             "10.188.111.254"])

    def test_nao_repete(self):
        self.assertEqual(ad.expandir_hosts("10.0.0.1, 10.0.0.1"), ["10.0.0.1"])

    def test_vazio(self):
        self.assertEqual(ad.expandir_hosts(""), [])
        self.assertEqual(ad.expandir_hosts(None), [])


class TesteConferencia(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="atu-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_banco_bom(self):
        serve, motivo, estradas = ad.conferir_banco(banco())
        self.assertTrue(serve, motivo)
        self.assertEqual(estradas, 325)

    def test_truncado(self):
        p = os.path.join(self.dir, "t.db")
        with open(banco(), "rb") as e, open(p, "wb") as s:
            s.write(e.read(3000))
        serve, motivo, _ = ad.conferir_banco(p)
        self.assertFalse(serve)

    def test_sqlite_sem_topologia(self):
        p = os.path.join(self.dir, "outro.db")
        c = sqlite3.connect(p)
        c.executescript("CREATE TABLE Equipment(Id INTEGER);")
        c.commit(); c.close()
        serve, motivo, _ = ad.conferir_banco(p)
        self.assertFalse(serve)
        self.assertIn("GeographicRegion", motivo)

    def test_nao_e_sqlite(self):
        p = os.path.join(self.dir, "x.db")
        with open(p, "wb") as f:
            f.write(b"a" * 9000)
        serve, motivo, _ = ad.conferir_banco(p)
        self.assertFalse(serve)
        self.assertIn("SQLite", motivo)


class FixturaAtualizador:
    """Banco de verdade num diretorio temporario e SSH sempre no ar."""

    @classmethod
    def setUpClass(cls):
        cls.original = ad.porta_aberta
        ad.porta_aberta = lambda host, porta=22, timeout=1.5: True

    @classmethod
    def tearDownClass(cls):
        ad.porta_aberta = cls.original

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="atu-")
        self.destino = os.path.join(self.dir, "Modular.db")
        shutil.copy2(banco(), self.destino)
        self.bom = banco()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _banco_diferente(self):
        """Copia do banco bom com um byte a mais de sobra: hash diferente,
           conteudo valido."""
        p = os.path.join(self.dir, "novo.db")
        shutil.copy2(self.bom, p)
        c = sqlite3.connect(p)
        c.execute("CREATE TABLE Marca(x INTEGER)")
        c.commit(); c.close()
        return p

    def monta(self, clientes, **kw):
        return ad.Atualizador(hosts=list(clientes), destino=self.destino,
                              senha="x", intervalo_s=3600,
                              fabrica_cliente=lambda h: clientes[h], **kw)


class TesteAtualizador(FixturaAtualizador, unittest.TestCase):
    def test_escolhe_o_mais_novo_e_nao_o_primeiro(self):
        """PTX esquecido num canto tem banco velho: quem manda e' a data."""
        novo = self._banco_diferente()
        clientes = {
            "10.0.0.1": ClienteFalso(self.bom, mtime=1000),
            "10.0.0.2": ClienteFalso(novo, mtime=9000),
            "10.0.0.3": ClienteFalso(self.bom, mtime=5000),
        }
        at = self.monta(clientes)
        r = at.atualizar_agora()
        self.assertTrue(r["ok"], r.get("motivo"))
        self.assertTrue(r["trocou"])
        self.assertEqual(r["host"], "10.0.0.2")
        self.assertEqual(clientes["10.0.0.2"].baixou, 1)
        self.assertEqual(clientes["10.0.0.1"].baixou, 0)

    def test_nao_troca_quando_e_o_mesmo_banco(self):
        clientes = {"10.0.0.1": ClienteFalso(self.bom, mtime=9000)}
        antes = open(self.destino, "rb").read()
        r = self.monta(clientes).atualizar_agora()
        self.assertTrue(r["ok"])
        self.assertFalse(r["trocou"])
        self.assertEqual(open(self.destino, "rb").read(), antes)
        self.assertFalse(os.path.exists(self.destino + ".anterior"))

    def test_banco_ruim_nao_derruba_o_bom(self):
        """A regra que mais importa: download quebrado nao pode virar o banco
           de producao e deixar a mina sem mapa."""
        ruim = os.path.join(self.dir, "ruim.db")
        with open(self.bom, "rb") as e, open(ruim, "wb") as s:
            s.write(e.read(5000))
        clientes = {"10.0.0.1": ClienteFalso(ruim, mtime=9999)}
        antes = open(self.destino, "rb").read()
        r = self.monta(clientes).atualizar_agora()
        self.assertFalse(r["ok"])
        self.assertIn("nao presta", r["motivo"])
        self.assertEqual(open(self.destino, "rb").read(), antes,
                         "o banco bom foi substituido por um quebrado")
        self.assertFalse(os.path.exists(self.destino + ".baixando"))

    def test_guarda_o_anterior_ao_trocar(self):
        novo = self._banco_diferente()
        antes = open(self.destino, "rb").read()
        clientes = {"10.0.0.1": ClienteFalso(novo, mtime=9000)}
        r = self.monta(clientes).atualizar_agora()
        self.assertTrue(r["trocou"])
        self.assertTrue(os.path.exists(self.destino + ".anterior"))
        self.assertEqual(open(self.destino + ".anterior", "rb").read(), antes)

    def test_avisa_quem_precisa_recarregar(self):
        novo = self._banco_diferente()
        chamou = []
        clientes = {"10.0.0.1": ClienteFalso(novo, mtime=9000)}
        at = self.monta(clientes, ao_atualizar=lambda: chamou.append(1))
        at.atualizar_agora()
        self.assertEqual(chamou, [1])

    def test_queda_no_download_nao_deixa_lixo(self):
        clientes = {"10.0.0.1": ClienteFalso(self.bom, mtime=9000,
                                             falhar_em="baixar")}
        r = self.monta(clientes).atualizar_agora()
        self.assertFalse(r["ok"])
        self.assertFalse(os.path.exists(self.destino + ".baixando"))

    def test_ptx_que_nao_responde_e_pulado(self):
        novo = self._banco_diferente()
        clientes = {
            "10.0.0.1": ClienteFalso(self.bom, mtime=9999, falhar_em="conectar"),
            "10.0.0.2": ClienteFalso(novo, mtime=100),
        }
        r = self.monta(clientes).atualizar_agora()
        self.assertTrue(r["ok"], r.get("motivo"))
        self.assertEqual(r["host"], "10.0.0.2")

    def test_nenhum_ptx_no_ar(self):
        ad.porta_aberta = lambda host, porta=22, timeout=1.5: False
        try:
            r = self.monta({"10.0.0.1": ClienteFalso(self.bom, 1)}).atualizar_agora()
            self.assertFalse(r["ok"])
            self.assertIn("nenhum PTX", r["motivo"])
        finally:
            ad.porta_aberta = lambda host, porta=22, timeout=1.5: True

    def test_estado_para_a_api(self):
        clientes = {"10.0.0.1": ClienteFalso(self.bom, mtime=9000)}
        at = self.monta(clientes)
        self.assertEqual(at.estado()["resultado"], "ainda nao rodou")
        at.atualizar_agora()
        e = at.estado()
        self.assertIsNotNone(e["quando"])
        self.assertEqual(e["hosts"], 1)


class TesteHostsDoBanco(unittest.TestCase):
    """A lista de terminais sai do proprio Modular.db.

       O DISPATCH guarda em HostAddress o nome do equipamento e o endereco
       dele: CA-1001 -> 10.188.98.1. Este projeto varria 10.188.98.1-50 na
       mao, e a mina tem 161 terminais, ate' o .242 — a busca do banco mais
       novo olhava um terco da frota e podia nunca ver o PTX que tinha a
       versao boa."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="hosts-")
        self.db = os.path.join(self.dir, "Modular.db")
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE HostAddress (Id INTEGER, Network TEXT, "
                    "Address TEXT, Host TEXT, Channel TEXT)")
        con.executemany("INSERT INTO HostAddress (Address, Host) VALUES (?,?)", [
            ("10.188.98.1", "CA-1001"),
            ("10.188.98.242", "CA-1242"),
            ("10.188.98.1", "CA-1001"),          # repetido
            ("10.188.112.23", "mtafbrcmd606"),   # servidor central
            ("10.188.102.7", "PA-5801"),
            ("", "CA-VAZIO"),
            (None, "CA-NULO"),
        ])
        con.commit(); con.close()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_le_os_terminais_e_nao_repete(self):
        h = ad.hosts_do_banco(self.db)
        self.assertEqual(h, ["10.188.98.1", "10.188.98.242", "10.188.102.7"])

    def test_deixa_o_servidor_central_de_fora(self):
        """O banco do central nao vem de um PTX; buscar nele nao faz sentido."""
        self.assertNotIn("10.188.112.23", ad.hosts_do_banco(self.db))

    def test_prefixo_restringe_a_rede(self):
        h = ad.hosts_do_banco(self.db, prefixo="10.188.98.")
        self.assertEqual(h, ["10.188.98.1", "10.188.98.242"])

    def test_banco_sem_a_tabela_nao_estoura(self):
        vazio = os.path.join(self.dir, "vazio.db")
        con = sqlite3.connect(vazio); con.execute("CREATE TABLE X(a)"); con.close()
        self.assertEqual(ad.hosts_do_banco(vazio), [])
        self.assertEqual(ad.hosts_do_banco(os.path.join(self.dir, "nao-existe.db")), [])

    def test_configuracao_com_hosts_banco(self):
        at = ad.de_configuracao({"hosts": "banco", "senha": "x"}, self.db)
        self.assertEqual(at.hosts, ["10.188.98.1", "10.188.98.242", "10.188.102.7"])

    def test_sem_o_banco_cai_na_faixa_de_reserva(self):
        """Primeira execucao, antes de existir banco: precisa varrer algo."""
        at = ad.de_configuracao({"hosts": "banco", "senha": "x"},
                                os.path.join(self.dir, "ainda-nao.db"))
        self.assertGreater(len(at.hosts), 200)
        self.assertIn("10.188.98.1", at.hosts)


class ClienteLerdo:
    """PTX que aceita a conexao e nao responde. E' o caso que faz o arranque
       travar se a espera nao tiver hora marcada."""

    def __init__(self, demora=30):
        self.demora = demora

    def conectar(self):
        time.sleep(self.demora)

    def info(self, caminho):
        time.sleep(self.demora)
        return (0, 0)

    def baixar(self, caminho, destino):
        time.sleep(self.demora)

    def fechar(self):
        pass


class TesteBuscaNoInicio(FixturaAtualizador, unittest.TestCase):
    """O servidor procura o banco mais novo antes de abrir a porta: quem liga
       de manha quer o mapa de hoje, nao o do ultimo desligamento."""

    def test_traz_o_banco_antes_de_abrir_a_porta(self):
        novo = self._banco_diferente()
        antes = ad.hash_curto(self.destino)
        at = self.monta({"10.0.0.1": ClienteFalso(novo, mtime=9000)})

        r = at.buscar_no_inicio(30)

        self.assertTrue(r["concluiu"])
        self.assertTrue(r["trocou"])
        self.assertNotEqual(ad.hash_curto(self.destino), antes,
                            "o banco novo tinha que estar no disco antes de servir")

    def test_desiste_no_limite_e_deixa_a_busca_correndo(self):
        """Servir a malha de ontem e' melhor que nao servir nada."""
        at = self.monta({"10.0.0.1": ClienteLerdo(30)})

        comeco = time.time()
        r = at.buscar_no_inicio(1)
        gasto = time.time() - comeco

        self.assertFalse(r["concluiu"])
        self.assertLess(gasto, 6, "o arranque ficou preso no PTX que nao responde")
        self.assertIn("segundo plano", r["motivo"])

    def test_uma_rodada_por_vez(self):
        """A rodada do arranque e a do laco periodico se encontram; duas
           baixando para o mesmo arquivo temporario e' briga."""
        at = self.monta({"10.0.0.1": ClienteLerdo(2)})
        t = threading.Thread(target=at.atualizar_agora, daemon=True)
        t.start()
        time.sleep(0.3)
        try:
            r = at.atualizar_agora()
            self.assertFalse(r["ok"])
            self.assertIn("em andamento", r["motivo"])
        finally:
            t.join(timeout=10)

    def test_o_laco_nao_repete_a_busca_que_o_arranque_ja_fez(self):
        at = self.monta({"10.0.0.1": ClienteFalso(self.bom, mtime=9000)})
        rodadas = []
        at.atualizar_agora = lambda: rodadas.append(1)

        at.iniciar(agora=False)
        time.sleep(0.4)
        try:
            self.assertEqual(rodadas, [],
                             "o laco repetiu no mesmo minuto a busca do arranque")
        finally:
            at.encerrar()


if __name__ == "__main__":
    unittest.main(verbosity=2)
