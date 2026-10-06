/*
 * PtxNav.cs — cliente de navegacao do PTX dedicado.
 *
 * O QUE FAZ
 *   1. Le a posicao propria DIRETO DA SERIAL (NMEA), com validacao de
 *      checksum e reconexao automatica. Nao depende de DISPATCH nem de
 *      captura de pacote: este PTX e' dedicado, nao roda DISPATCH.
 *   2. Sincroniza malha/locais/areas com o servidor de rotas e guarda em
 *      disco. So' baixa quando o hash muda (ETag). Funciona offline pelo cache.
 *   2b. Repassa a frota ao vivo (posicao dos equipamentos, lida do GPS dos
 *      radios Rajant pelo servidor) e a rota ate um equipamento escolhido.
 *   3. Serve a interface de navegacao no proprio PTX, SEM internet e SEM
 *      biblioteca externa: o desenho e' canvas puro.
 *   4. Opcional: reemite as sentencas NMEA numa porta TCP, para o BreadCrumb
 *      Rajant saber onde o equipamento esta'.
 *
 * PRINCIPIO: o C# nao interpreta o GeoJSON. Ele guarda os bytes e entrega;
 * quem interpreta e' o JavaScript da pagina. E' o que manteve o executavel
 * pequeno e o consumo baixo no terminal.
 *
 * AUFS: a raiz / do PTX tem a camada gravavel em RAM e some no reboot. Todo
 * cache e' gravado NAS DUAS CAMADAS: em /media/realroot (persiste) e em /
 * (vale ja', sem reiniciar).
 *
 * USO
 *   mono PtxNav.exe --servidor http://10.188.98.200:5000 \
 *                   --serial /dev/ttyS3 --baud auto --porta 8090
 */
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

// Fica em RAM de proposito, para nao gastar o eMMC. Por isso a rotacao e'
// verificada A CADA ESCRITA, e nao so' na inicializacao: o terminal pode
// passar meses sem reiniciar.
static class Log
{
    static readonly object Trava = new object();
    static string Arquivo;
    static long MaxBytes = 2L * 1024 * 1024;

    public static void Iniciar(string arquivo, long maxBytes)
    {
        Arquivo = arquivo; MaxBytes = maxBytes;
        if (string.IsNullOrEmpty(arquivo)) { Arquivo = null; return; }
        try
        {
            string dir = Path.GetDirectoryName(arquivo);
            if (dir != null && dir.Length > 0 && !Directory.Exists(dir))
                Directory.CreateDirectory(dir);
        }
        catch (Exception e) { Console.WriteLine("[log] sem arquivo de log: " + e.Message); Arquivo = null; }
    }

    public static void E(string tag, string msg)
    {
        string linha = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss",
                           CultureInfo.InvariantCulture) + " [" + tag + "] " + msg;
        Console.WriteLine(linha);
        if (Arquivo == null) return;
        lock (Trava)
        {
            try
            {
                var fi = new FileInfo(Arquivo);
                if (fi.Exists && fi.Length > MaxBytes) Rotaciona();
                File.AppendAllText(Arquivo, linha + "\n", Encoding.UTF8);
            }
            catch { /* log nunca pode derrubar a aplicacao */ }
        }
    }

    static void Rotaciona()
    {
        string velho = Arquivo + ".1";
        try { if (File.Exists(velho)) File.Delete(velho); } catch { }
        try { File.Move(Arquivo, velho); } catch { try { File.Delete(Arquivo); } catch { } }
    }
}

// Grava sempre nas duas camadas do aufs. Le da primeira que tiver o arquivo.
static class Cache
{
    public static string Ram = "/opt/ptxnav/cache";
    public static string Persistente = "/media/realroot/opt/ptxnav/cache";

    public static IEnumerable<string> Camadas()
    {
        if (!string.IsNullOrEmpty(Ram)) yield return Ram;
        if (!string.IsNullOrEmpty(Persistente) && Persistente != Ram) yield return Persistente;
    }

    public static void Preparar()
    {
        foreach (string c in Camadas())
        {
            try { Directory.CreateDirectory(c); }
            catch (Exception e) { Log.E("cache", "nao consegui criar " + c + ": " + e.Message); }
        }
    }

    /// Gravacao atomica (tmp + move) em cada camada. Nunca deixa arquivo pela metade.
    public static bool Grava(string nome, string texto)
    {
        bool algum = false;
        foreach (string c in Camadas())
        {
            string p = Path.Combine(c, nome);
            try
            {
                File.WriteAllText(p + ".tmp", texto, Encoding.UTF8);
                if (File.Exists(p)) File.Delete(p);
                File.Move(p + ".tmp", p);
                algum = true;
            }
            catch (Exception e) { Log.E("cache", "falhou gravar " + p + ": " + e.Message); }
        }
        return algum;
    }

    public static string Le(string nome)
    {
        foreach (string c in Camadas())
        {
            string p = Path.Combine(c, nome);
            try { if (File.Exists(p)) return File.ReadAllText(p, Encoding.UTF8); }
            catch (Exception e) { Log.E("cache", "falhou ler " + p + ": " + e.Message); }
        }
        return "";
    }

    public static bool Tem(string nome)
    {
        foreach (string c in Camadas())
            try { if (File.Exists(Path.Combine(c, nome))) return true; } catch { }
        return false;
    }
}

/// Decodificacao de sentencas NMEA 0183. Sem estado: da' para testar sozinha.
static class Nmea
{
    /// XOR de todos os bytes entre '$' e '*'. Serial suja e' comum em veiculo:
    /// linha que nao fecha o checksum e' descartada.
    public static bool ChecksumOk(string linha)
    {
        if (linha == null) return false;
        int i = linha.IndexOf('$');
        if (i < 0) return false;
        int a = linha.IndexOf('*', i);
        if (a < 0 || a + 2 >= linha.Length) return false;
        int x = 0;
        for (int k = i + 1; k < a; k++)
        {
            char ch = linha[k];
            if (ch > 0xFF) return false;
            x ^= (byte)ch;
        }
        int dado;
        if (!int.TryParse(linha.Substring(a + 1, 2), NumberStyles.HexNumber,
                          CultureInfo.InvariantCulture, out dado)) return false;
        return x == dado;
    }

    /// "GPRMC", "GNGGA"... Vazio se a linha nao tiver forma de sentenca.
    public static string Tipo(string linha)
    {
        int i = linha.IndexOf('$');
        if (i < 0 || i + 6 > linha.Length) return "";
        return linha.Substring(i + 1, 5).ToUpperInvariant();
    }

    /// Aceita GPS, GNSS misto, GLONASS e Galileo.
    public static bool TalkerAceito(string tipo)
    {
        if (tipo.Length < 5) return false;
        string t = tipo.Substring(0, 2);
        return t == "GP" || t == "GN" || t == "GL" || t == "GA";
    }

    /// ddmm.mmmm / dddmm.mmmm -> graus decimais. Hemisferio S ou W vira negativo.
    public static double Graus(string campo, string hemisferio)
    {
        if (string.IsNullOrEmpty(campo)) return 0.0;
        double v;
        if (!double.TryParse(campo, NumberStyles.Float, CultureInfo.InvariantCulture, out v))
            return 0.0;
        double g = Math.Floor(v / 100.0);
        double min = v - g * 100.0;
        double r = g + min / 60.0;
        if (hemisferio == "S" || hemisferio == "W" || hemisferio == "s" || hemisferio == "w")
            r = -r;
        return r;
    }

    public static double Num(string campo)
    {
        double v;
        return double.TryParse(campo, NumberStyles.Float, CultureInfo.InvariantCulture, out v) ? v : 0.0;
    }
}

/// Posicao lida do receptor.
class Fix
{
    public double Lat, Lon, CogGraus, SogNos, Altitude;
    public int Qualidade = -1;      // do GGA; 0 = sem fix
    public bool Valido;             // do RMC ('A') ou GGA (qualidade > 0)
    public DateTime Quando = DateTime.MinValue;
    public double SogKmh { get { return SogNos * 1.852; } }
}

class PtxNav
{
    static string Servidor = "http://127.0.0.1:5000";
    static int PortaLocal = 8090;
    static string Serial = "/dev/ttyxx3";    // "auto" procura em todas as seriais
    static string BaudPedido = "auto";
    static int SincSeg = 300;
    static int NmeaTcp = 0;                 // 0 = nao reemite
    static string ArquivoLog = "/var/log/ptxnav.log";
    static long LogMax = 2L * 1024 * 1024;
    static double LimiteParadoKmh = 3.0;    // acima disso a interface trava
    static int SegSemFix = 10;              // secao 2: fix velho e' fix invalido
    static bool ModoTeste = false;          // --testar: diagnostico e sai
    static bool ModoProcurar = false;       // --procurar-gps: varre as seriais

    // baud na ordem da especificacao (9600 e' o mais comum)
    static readonly int[] BaudsCandidatos = { 9600, 4800, 38400, 115200 };

    static readonly object Trava = new object();
    static Fix Atual = new Fix();
    static string Callsign = "";
    static volatile string EstadoServidor = "nunca contatado";
    static volatile string EstadoGps = "abrindo a serial";
    static volatile string HashLocal = "";
    static volatile string QuandoSinc = "nunca";
    static volatile int BaudEmUso = 0;
    static volatile string SerialEmUso = "";   // resolvido quando SERIAL=auto
    static long LinhasBoas, LinhasRuins;

    static volatile Stream SerialAberta = null;
    static DateTime UltimoByte = DateTime.MinValue;

    // Ultima frota conhecida. Fica so' em memoria de proposito: e' dado que
    // muda a cada ciclo, e grava-lo em disco gastaria o eMMC sem necessidade.
    static readonly object TravaFrota = new object();
    static string FrotaJson = "";
    static DateTime FrotaQuando = DateTime.MinValue;

    // Areas de exclusao de desmonte. Mudam uma vez por semana, entao vale
    // guardar em disco: se o servidor cair na hora do fogo, o operador ainda
    // ve' a area.
    static readonly object TravaDesmonte = new object();
    static string DesmonteJson = "";

    // clientes TCP que querem as sentencas (BreadCrumb Rajant)
    static readonly List<TcpClient> Ouvintes = new List<TcpClient>();

    static void Main(string[] args)
    {
        Thread.CurrentThread.CurrentCulture = CultureInfo.InvariantCulture;
        if (!LerArgumentos(args)) return;

        if (ModoProcurar)
        {
            Callsign = Environment.MachineName.ToUpperInvariant();
            Environment.Exit(ProcurarGps());
            return;
        }

        if (ModoTeste)
        {
            Callsign = Environment.MachineName.ToUpperInvariant();
            Environment.Exit(Testar());
            return;
        }

        Log.Iniciar(ArquivoLog, LogMax);
        Callsign = Environment.MachineName.ToUpperInvariant();
        Cache.Preparar();
        HashLocal = Cache.Le("hash.txt").Trim();

        // endereco escolhido no menu vence o do ptxnav.conf: quem mexeu por
        // ultimo foi o tecnico, na frente do equipamento
        string local = Cache.Le("config_local.json");
        if (local != null && local.Length > 5)
        {
            string url = Campo(local, "servidor");
            if (url != null && url.Length > 0)
            {
                Servidor = url.TrimEnd('/');
                Log.E("inicio", "servidor vindo do menu: " + Servidor);
            }
        }

        Log.E("inicio", "PTX=" + Callsign + " servidor=" + Servidor);
        Log.E("inicio", "serial=" + Serial + " baud=" + BaudPedido);
        Log.E("inicio", "cache RAM=" + Cache.Ram + " persistente=" + Cache.Persistente);
        Log.E("inicio", "cache atual: " + (HashLocal == "" ? "vazio" : HashLocal));

        Iniciar(LerGps, "gps");
        Iniciar(VigiaSerial, "vigia");
        Iniciar(Sincroniza, "sinc");
        if (NmeaTcp > 0) Iniciar(ServirNmea, "nmea-tcp");
        Servir();
    }

    static void Iniciar(ThreadStart f, string nome)
    {
        var t = new Thread(delegate ()
        {
            Thread.CurrentThread.CurrentCulture = CultureInfo.InvariantCulture;
            try { f(); }
            catch (Exception e) { Log.E(nome, "thread morreu: " + e.Message); }
        });
        t.IsBackground = true; t.Name = nome; t.Start();
    }

    static bool LerArgumentos(string[] args)
    {
        for (int i = 0; i < args.Length; i++)
        {
            try
            {
                switch (args[i])
                {
                    case "--servidor":   Servidor = args[++i].TrimEnd('/'); break;
                    case "--porta":      PortaLocal = int.Parse(args[++i]); break;
                    case "--serial":     Serial = args[++i]; break;
                    case "--baud":       BaudPedido = args[++i]; break;
                    case "--cache":      Cache.Ram = args[++i]; break;
                    case "--cache-persistente": Cache.Persistente = args[++i]; break;
                    case "--intervalo":  SincSeg = int.Parse(args[++i]); break;
                    case "--nmea-tcp":   NmeaTcp = int.Parse(args[++i]); break;
                    case "--log":        ArquivoLog = args[++i]; break;
                    case "--log-max-kb": LogMax = long.Parse(args[++i]) * 1024L; break;
                    case "--limite-parado": LimiteParadoKmh = double.Parse(args[++i], CultureInfo.InvariantCulture); break;
                    case "--seg-sem-fix":   SegSemFix = int.Parse(args[++i]); break;
                    case "--testar":        ModoTeste = true; break;
                    case "--procurar-gps":  ModoProcurar = true; break;
                    case "--ajuda":
                    case "-h":
                    case "--help": Ajuda(); return false;
                    default:
                        Console.WriteLine("argumento desconhecido: " + args[i]);
                        Ajuda(); return false;
                }
            }
            catch (Exception e)
            {
                Console.WriteLine("erro no argumento " + args[i] + ": " + e.Message);
                return false;
            }
        }
        return true;
    }

    static void Ajuda()
    {
        Console.WriteLine(
@"PtxNav — navegacao do PTX dedicado

  --servidor URL          servidor de rotas          (padrao http://127.0.0.1:5000)
  --porta N               porta da interface local   (padrao 8090)
  --serial DEV|auto       device do GPS; auto procura sozinho (padrao auto)
  --baud auto|9600|...    velocidade da serial       (padrao auto)
  --cache DIR             cache em RAM               (padrao /opt/ptxnav/cache)
  --cache-persistente DIR cache que sobrevive ao reboot
                                                     (padrao /media/realroot/opt/ptxnav/cache)
  --intervalo N           segundos entre sincronizacoes (padrao 300)
  --nmea-tcp N            reemite NMEA nesta porta TCP (0 = nao reemite)
  --log ARQ               arquivo de log             (padrao /var/log/ptxnav.log)
  --log-max-kb N          tamanho antes de rotacionar (padrao 2048)
  --limite-parado N       km/h abaixo do qual o veiculo conta como parado (padrao 3)
  --seg-sem-fix N         segundos sem sentenca para invalidar o fix (padrao 10)
  --testar                confere serial, servidor e cache, imprime o laudo e sai
  --procurar-gps          varre todas as seriais e diz em qual delas ha GPS");
    }

    // "mono PtxNav.exe --testar" confere, no proprio terminal, as tres coisas
    // que costumam estar erradas em campo: o device da serial, o alcance ate o
    // servidor e a gravacao nas duas camadas do aufs. Nao instala nem altera
    // nada; imprime um laudo e sai.
    static int Testar()
    {
        int falhas = 0;
        Console.WriteLine("===============================================");
        Console.WriteLine(" TESTE DA NAVEGACAO - " + Callsign);
        Console.WriteLine(" " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss",
                                                      CultureInfo.InvariantCulture));
        Console.WriteLine("===============================================");
        falhas += TestaSerial();
        falhas += TestaServidor();
        falhas += TestaCache();

        Console.WriteLine();
        Console.WriteLine("===============================================");
        if (falhas == 0)
            Console.WriteLine(" TUDO CERTO. Pode instalar com instalar_ptx.sh");
        else
            Console.WriteLine(" " + falhas + " problema(s) acima. Resolva antes de instalar.");
        Console.WriteLine("===============================================");
        return falhas == 0 ? 0 : 1;
    }

    static void Titulo(string t)
    {
        Console.WriteLine();
        Console.WriteLine("--- " + t + " " + new string('-', Math.Max(0, 44 - t.Length)));
    }

    static int Falha(string msg, string dica)
    {
        Console.WriteLine("  FALHA  " + msg);
        if (dica != null) Console.WriteLine("         -> " + dica);
        return 1;
    }

    static void Ok(string msg) { Console.WriteLine("  ok     " + msg); }

    static int TestaSerial()
    {
        Titulo("1. GPS na serial");
        Console.WriteLine("  device configurado: " + Serial);

        if (Serial == "auto" || Serial.Length == 0)
        {
            Console.WriteLine("  procurando o GPS nas seriais deste terminal...");
            int baudAuto;
            string achado = AcharGpsAutomatico(out baudAuto);
            if (achado == null)
                return Falha("nenhuma serial deste terminal tem NMEA",
                    "rode 'mono PtxNav.exe --procurar-gps' para ver device por device");
            Ok("GPS encontrado sozinho em " + achado + " a " + baudAuto + " baud");
            Serial = achado;
            BaudPedido = baudAuto.ToString(CultureInfo.InvariantCulture);
        }

        if (!File.Exists(Serial))
        {
            int r = Falha(Serial + " nao existe",
                "rode 'mono PtxNav.exe --procurar-gps' para achar o device certo");
            ListaCandidatos();
            return r;
        }

        int baud;
        if (BaudPedido == "auto")
        {
            Console.WriteLine("  procurando a velocidade (9600, 4800, 38400, 115200)...");
            baud = DescobrirBaud(Serial);
            if (baud <= 0)
                return Falha("nenhuma velocidade deu sentenca NMEA valida em " + Serial,
                    "rode 'mono PtxNav.exe --procurar-gps' para achar o device certo");
        }
        else
        {
            baud = int.Parse(BaudPedido);
            ConfiguraStty(Serial, baud);
        }
        Ok("velocidade: " + baud + " baud");

        var tipos = new Dictionary<string, int>();
        int boas = 0, ruins = 0, total = 0;
        var visto = new Fix();
        try
        {
            using (var s = Abrir(Serial))
            {
                var buf = new byte[512];
                var linha = new StringBuilder(128);
                DateTime fim = DateTime.UtcNow.AddSeconds(8);
                Console.WriteLine("  lendo 8 segundos...");
                while (DateTime.UtcNow < fim)
                {
                    int n = s.Read(buf, 0, buf.Length);
                    if (n <= 0) break;
                    for (int i = 0; i < n; i++)
                    {
                        char c = (char)buf[i];
                        if (c == '\n' || c == '\r')
                        {
                            if (linha.Length == 0) continue;
                            string ln = linha.ToString(); linha.Length = 0;
                            total++;
                            if (!Nmea.ChecksumOk(ln)) { ruins++; continue; }
                            boas++;
                            string t = Nmea.Tipo(ln);
                            if (!tipos.ContainsKey(t)) tipos[t] = 0;
                            tipos[t]++;
                            string[] cc = ln.Split(',');
                            if (t.Length == 5 && t.Substring(2) == "RMC" && cc.Length > 6
                                && cc[2] == "A" && cc[3].Length > 0)
                            {
                                visto.Lat = Nmea.Graus(cc[3], cc[4]);
                                visto.Lon = Nmea.Graus(cc[5], cc[6]);
                                visto.SogNos = Nmea.Num(cc[7]);
                                visto.Valido = true;
                            }
                            else if (t.Length == 5 && t.Substring(2) == "GGA" && cc.Length > 6
                                     && cc[2].Length > 0 && Nmea.Num(cc[6]) > 0)
                            {
                                visto.Lat = Nmea.Graus(cc[2], cc[3]);
                                visto.Lon = Nmea.Graus(cc[4], cc[5]);
                                visto.Valido = true;
                            }
                        }
                        else if (linha.Length < 120) linha.Append(c);
                        else linha.Length = 0;
                    }
                }
            }
        }
        catch (Exception e)
        {
            return Falha(ExplicaFalhaDeAbertura(Serial, e.Message),
                "rode 'mono PtxNav.exe --procurar-gps' para achar o device certo");
        }

        if (total == 0)
            return Falha("o device existe mas nao veio nada em 8 s",
                "receptor desligado, cabo solto, ou e' outro device");

        Ok(total + " linhas, " + boas + " com checksum bom, " + ruins + " descartadas");
        var sb = new StringBuilder();
        foreach (var kv in tipos) { if (sb.Length > 0) sb.Append(", "); sb.Append(kv.Key).Append("=").Append(kv.Value); }
        Console.WriteLine("         sentencas: " + sb);

        int problemas = 0;
        if (boas == 0)
            problemas += Falha("nenhuma linha passou no checksum",
                "velocidade errada ou serial muito suja");
        if (ruins > boas && boas > 0)
            Console.WriteLine("  AVISO  mais linhas ruins que boas: confira o cabo");
        bool temRmc = false, temGga = false;
        foreach (var kv in tipos)
        {
            if (kv.Key.Length == 5 && kv.Key.Substring(2) == "RMC") temRmc = true;
            if (kv.Key.Length == 5 && kv.Key.Substring(2) == "GGA") temGga = true;
        }
        if (!temRmc && !temGga)
            problemas += Falha("nao veio RMC nem GGA",
                "sao essas as sentencas que trazem posicao; configure o receptor");
        else if (!temRmc)
            Console.WriteLine("  AVISO  sem RMC: a velocidade (trava de seguranca) nao vai funcionar");

        if (visto.Valido)
        {
            Ok("posicao: " + N(visto.Lat) + ", " + N(visto.Lon) +
               "   (" + N(Math.Round(visto.SogNos * 1.852, 1)) + " km/h)");
            if (visto.Lat > 0)
                Console.WriteLine("  AVISO  latitude positiva: no Brasil deveria ser negativa (S)");
            if (visto.Lon > 0)
                Console.WriteLine("  AVISO  longitude positiva: no Brasil deveria ser negativa (W)");
        }
        else
        {
            Console.WriteLine("  AVISO  chegou NMEA, mas ainda SEM FIX valido.");
            Console.WriteLine("         Normal sob cobertura; leve o equipamento para ceu aberto.");
        }
        return problemas;
    }

    static void ListaCandidatos()
    {
        Console.WriteLine("         devices seriais encontrados agora:");
        bool achou = false;
        foreach (string padrao in new[] { "ttyS", "ttyUSB", "ttyACM", "ttyO", "ttyAMA" })
        {
            try
            {
                foreach (string f in Directory.GetFiles("/dev", padrao + "*"))
                {
                    Console.WriteLine("           " + f);
                    achou = true;
                }
            }
            catch { }
        }
        if (!achou) Console.WriteLine("           nenhum");
    }

    static int TestaServidor()
    {
        Titulo("2. Servidor de rotas");
        Console.WriteLine("  endereco: " + Servidor);
        var relogio = Stopwatch.StartNew();
        Resposta r;
        try
        {
            r = Baixar(Servidor + "/api/saude", null);
        }
        catch (Exception e)
        {
            return Falha("nao alcancei o servidor: " + e.Message,
                "confira o IP, se o servidor_rotas.exe esta rodando, e o firewall "
                + "do Windows na porta do servidor");
        }
        relogio.Stop();
        if (r.Status != 200)
            return Falha("o servidor respondeu HTTP " + r.Status, "confira o log dele");

        Ok("respondeu em " + relogio.ElapsedMilliseconds + " ms");
        string hash = Campo(r.Corpo, "hash");
        string versao = Campo(r.Corpo, "versao");
        if (versao.Length > 0) Console.WriteLine("         versao do servidor: " + versao);
        if (hash.Length > 0) Console.WriteLine("         versao dos dados: " + hash);

        int problemas = 0;
        if (r.Corpo.IndexOf("\"ok\": true") < 0 && r.Corpo.IndexOf("\"ok\":true") < 0)
            problemas += Falha("o servidor esta no ar mas diz que nao carregou a malha",
                "confira o caminho do Modular.db no servidor");

        try
        {
            var m = Baixar(Servidor + "/api/malha", null);
            if (m.Status == 200)
                Ok("malha viaria: " + (m.Corpo.Length / 1024) + " KB");
            else
                problemas += Falha("/api/malha respondeu HTTP " + m.Status, null);
        }
        catch (Exception e) { problemas += Falha("/api/malha falhou: " + e.Message, null); }

        try
        {
            var f = Baixar(Servidor + "/api/equipamentos", null);
            if (f.Status == 200)
            {
                string total = Numero(f.Corpo, "com_fix");
                string tt = Numero(f.Corpo, "total");
                Ok("frota Rajant: " + tt + " radios, " + total + " com posicao");
                if (total == "0")
                    Console.WriteLine("  AVISO  nenhum radio com fix: confira a senha e o "
                                    + "gpsSwitch no BC Commander");
            }
            else if (f.Status == 503)
            {
                Console.WriteLine("  AVISO  a camada de frota nao esta ligada no servidor.");
                Console.WriteLine("         Sem ela nao da para tracar rota ate equipamento.");
                Console.WriteLine("         Suba o servidor com --rajant-cache e --rajant-senha.");
            }
            else problemas += Falha("/api/equipamentos respondeu HTTP " + f.Status, null);
        }
        catch (Exception e) { problemas += Falha("/api/equipamentos falhou: " + e.Message, null); }

        return problemas;
    }

    /// Extrator minimo de campo numerico, para o laudo.
    static string Numero(string json, string campo)
    {
        string chave = "\"" + campo + "\"";
        int i = json.IndexOf(chave); if (i < 0) return "?";
        i = json.IndexOf(':', i); if (i < 0) return "?";
        int j = i + 1;
        while (j < json.Length && (json[j] == ' ')) j++;
        int k = j;
        while (k < json.Length && (char.IsDigit(json[k]) || json[k] == '-' || json[k] == '.')) k++;
        return k > j ? json.Substring(j, k - j) : "?";
    }

    static int TestaCache()
    {
        Titulo("3. Cache nas duas camadas (aufs)");
        int problemas = 0;
        string marca = "teste-" + DateTime.UtcNow.Ticks;

        foreach (string dir in new[] { Cache.Ram, Cache.Persistente })
        {
            string nome = dir == Cache.Ram ? "camada em RAM   " : "camada que persiste";

            // O teste que importa: a camada "persistente" esta mesmo em outra
            // montagem? Se ela cair na raiz, esta na camada em RAM do aufs — a
            // gravacao funciona, o teste passaria, e tudo sumiria no reboot.
            if (dir == Cache.Persistente)
            {
                string monte = MontagemDe(dir);
                if (monte == "/")
                {
                    problemas += Falha(
                        dir + " esta na raiz, ou seja, na camada em RAM do aufs",
                        "isso some no reboot. Ache o disco de verdade com "
                        + "'mount | grep -E \"mmcblk|realroot\"' e aponte "
                        + "CACHE_PERSISTENTE para la");
                    continue;
                }
                Console.WriteLine("         montagem: " + monte);
            }

            try
            {
                Directory.CreateDirectory(dir);
                string p = Path.Combine(dir, "teste_gravacao.txt");
                File.WriteAllText(p, marca);
                string lido = File.ReadAllText(p);
                File.Delete(p);
                if (lido != marca)
                    problemas += Falha(nome + " " + dir + ": gravou diferente do que leu", null);
                else
                    Ok(nome + " " + dir);
            }
            catch (Exception e)
            {
                problemas += Falha(nome + " " + dir + ": " + e.Message,
                    dir == Cache.Persistente
                        ? "sem esta camada a instalacao some no reboot. Confira: mount | grep realroot"
                        : "confira as permissoes");
            }
        }

        if (Cache.Tem("malha.json"))
            Ok("ja existe mapa em cache neste terminal");
        else
            Console.WriteLine("         (ainda sem mapa em cache: normal antes da 1a sincronizacao)");
        return problemas;
    }

    /// Ponto de montagem que contem o caminho. "/" significa que ele esta na
    /// camada gravavel em RAM do aufs — nao sobrevive ao reboot.
    static string MontagemDe(string caminho)
    {
        string melhor = "/";
        try
        {
            string alvo = Path.GetFullPath(caminho);
            foreach (string linha in File.ReadAllLines("/proc/mounts"))
            {
                string[] c = linha.Split(' ');
                if (c.Length < 2) continue;
                string ponto = c[1].Replace("\\040", " ");
                if (ponto == "/") continue;
                if ((alvo + "/").StartsWith(ponto.TrimEnd('/') + "/") &&
                    ponto.Length > melhor.Length)
                    melhor = ponto;
            }
        }
        catch { }
        return melhor;
    }

    static void LerGps()
    {
        while (true)
        {
            int baud = 0;
            string dev = Serial;
            try
            {
                // SERIAL=auto: procura o GPS em vez de exigir o nome do device.
                // Adivinhar esse nome foi o que mais atrapalhou em campo.
                if (Serial == "auto" || Serial.Length == 0)
                {
                    EstadoGps = "procurando o GPS nas seriais";
                    Log.E("gps", EstadoGps);
                    dev = AcharGpsAutomatico(out baud);
                    if (dev == null)
                    {
                        EstadoGps = "nenhuma serial deste terminal tem NMEA";
                        Log.E("gps", EstadoGps + "; tento de novo em 30 s");
                        Thread.Sleep(30000);
                        continue;
                    }
                    Log.E("gps", "GPS encontrado em " + dev + " a " + baud + " baud");
                }
                else
                {
                    // Tenta o device configurado. Se ele nao existir ou nao
                    // falar NMEA, procura nas outras em vez de ficar parado:
                    // nome de device errado nao pode deixar o veiculo sem mapa.
                    bool serve = false;
                    if (!File.Exists(dev))
                        Log.E("gps", "o device configurado (" + dev + ") nao existe");
                    else
                    {
                        baud = BaudPedido == "auto" ? DescobrirBaud(dev)
                                                    : int.Parse(BaudPedido);
                        serve = baud > 0;
                        if (!serve)
                            Log.E("gps", "o device configurado (" + dev + ") nao deu NMEA");
                    }
                    if (!serve)
                    {
                        Log.E("gps", "procurando o GPS nas outras seriais");
                        string outro = AcharGpsAutomatico(out baud);
                        if (outro == null)
                        {
                            EstadoGps = "nenhuma serial deste terminal tem NMEA";
                            Log.E("gps", EstadoGps + "; tento de novo em 30 s");
                            Thread.Sleep(30000);
                            continue;
                        }
                        Log.E("gps", "usando " + outro + " a " + baud +
                                     " baud (o configurado era " + dev + ")");
                        dev = outro;
                    }
                }
                ConfiguraStty(dev, baud);
                BaudEmUso = baud;
                SerialEmUso = dev;
                using (var s = Abrir(dev))
                {
                    SerialAberta = s;
                    UltimoByte = DateTime.UtcNow;
                    EstadoGps = "lendo " + dev + " a " + baud + " baud";
                    Log.E("gps", EstadoGps);
                    LacoDeLeitura(s);
                }
            }
            catch (Exception e)
            {
                EstadoGps = "serial caiu: " + e.Message;
                Log.E("gps", EstadoGps);
            }
            finally { SerialAberta = null; }
            // cabo solto, reboot do receptor: tenta de novo, sempre
            Thread.Sleep(3000);
        }
    }

    static FileStream Abrir(string dev)
    {
        // FileShare.ReadWrite: outro processo (bridge antigo, diagnostico) pode
        // estar lendo o mesmo device
        return new FileStream(dev, FileMode.Open, FileAccess.Read,
                              FileShare.ReadWrite, 1, false);
    }

    /// O kernel declara /dev/ttyS0..S3 mesmo sem placa serial atras. Abrir um
    /// desses devolve um erro seco ("Invalid handle to path"), que nao explica
    /// nada a quem esta em campo. Esta funcao traduz.
    static string ExplicaFalhaDeAbertura(string dev, string erro)
    {
        string e = (erro ?? "").ToLowerInvariant();
        if (e.IndexOf("invalid handle") >= 0 || e.IndexOf("no such device") >= 0 ||
            e.IndexOf("nxio") >= 0)
            return dev + " existe no /dev mas nao ha hardware atras dele "
                 + "(porta declarada pelo kernel, sem placa). Nao e' este o GPS.";
        if (e.IndexOf("permission") >= 0 || e.IndexOf("denied") >= 0)
            return dev + ": sem permissao. Rode como root.";
        if (e.IndexOf("busy") >= 0)
            return dev + ": ocupado por outro processo. Veja com: fuser -v " + dev;
        return dev + ": " + erro;
    }

    static string Curto(string m)
    {
        if (m == null) return "";
        m = m.Replace("\n", " ").Replace("\r", " ");
        return m.Length > 60 ? m.Substring(0, 57) + "..." : m;
    }

    /// Devices que podem ser uma serial de verdade.
    ///
    /// Aceita QUALQUER /dev/tty* e exclui o que se sabe que nao e' serial.
    /// A versao anterior tinha uma lista de prefixos conhecidos (ttyS, ttyUSB,
    /// ttyACM...) e por isso nao enxergava nome de driver de fabricante — foi
    /// o caso deste terminal. Lista de exclusao erra menos que lista de
    /// inclusao quando o hardware e' desconhecido.
    ///
    /// Ficam de fora, de proposito:
    ///   /dev/tty            terminal de controle do proprio processo
    ///   /dev/tty0../dev/tty63  consoles virtuais — abrir mexeria com a tela
    ///   /dev/ttyprintk      log do kernel, nao e' porta
    static bool EhConsoleVirtual(string nome)
    {
        if (nome == "tty" || nome == "ttyprintk" || nome == "console") return true;
        if (!nome.StartsWith("tty")) return false;
        string resto = nome.Substring(3);
        if (resto.Length == 0) return true;
        foreach (char c in resto) if (!char.IsDigit(c)) return false;
        return true;      // tty seguido so' de digitos = console virtual
    }

    static List<string> CandidatosSeriais()
    {
        var lista = new List<string>();
        try
        {
            foreach (string f in Directory.GetFiles("/dev", "tty*"))
            {
                string nome = Path.GetFileName(f);
                if (!EhConsoleVirtual(nome)) lista.Add(f);
            }
        }
        catch { }
        // links estaveis de GPS USB, quando existirem
        foreach (string d in new[] { "/dev/serial/by-id", "/dev/serial/by-path" })
        {
            try
            {
                if (Directory.Exists(d))
                    foreach (string f in Directory.GetFiles(d)) lista.Add(f);
            }
            catch { }
        }
        lista.Sort(StringComparer.Ordinal);
        return lista;
    }

    /// Le o device por alguns segundos SEM correr o risco de travar: a leitura
    /// roda numa thread e o stream e' fechado quando o prazo acaba, o que faz o
    /// Read estourar e devolver o controle.
    static string LerAmostra(string dev, double segundos, out int bytes,
                             out int boas, out int total)
    {
        int cBytes = 0, cBoas = 0, cTotal = 0;
        string exemplo = "";
        var s = Abrir(dev);
        var linha = new StringBuilder(128);
        var terminou = new ManualResetEvent(false);
        var t = new Thread(delegate ()
        {
            try
            {
                var buf = new byte[512];
                while (true)
                {
                    int n = s.Read(buf, 0, buf.Length);
                    if (n <= 0) { Thread.Sleep(50); continue; }   // timeout do stty
                    cBytes += n;
                    for (int i = 0; i < n; i++)
                    {
                        char c = (char)buf[i];
                        if (c == '\n' || c == '\r')
                        {
                            if (linha.Length == 0) continue;
                            string ln = linha.ToString(); linha.Length = 0;
                            cTotal++;
                            if (Nmea.ChecksumOk(ln))
                            {
                                cBoas++;
                                if (exemplo.Length == 0) exemplo = ln;
                            }
                        }
                        else if (linha.Length < 120) linha.Append(c);
                        else linha.Length = 0;
                    }
                }
            }
            catch { }
            terminou.Set();
        });
        t.IsBackground = true;
        t.Start();
        terminou.WaitOne((int)(segundos * 1000));
        try { s.Close(); } catch { }
        t.Join(1500);
        bytes = cBytes; boas = cBoas; total = cTotal;
        return exemplo;
    }

    /// Testa um device inteiro e devolve o baud em que apareceu NMEA valido,
    /// ou 0. Usada tanto pela varredura verbosa (--procurar-gps) quanto pela
    /// descoberta automatica do servico, para as duas nunca divergirem.
    static int SondaDevice(string dev, out string exemplo, out string erro,
                           out bool veioAlgo)
    {
        exemplo = ""; erro = null; veioAlgo = false;
        try { using (var teste = Abrir(dev)) { } }
        catch (Exception e) { erro = e.Message; return 0; }

        bool mudo = true;
        for (int i = 0; i < BaudsCandidatos.Length; i++)
        {
            int baud = BaudsCandidatos[i];
            ConfiguraStty(dev, baud, 20);
            int bytes, boas, total;
            string amostra;
            try
            {
                amostra = LerAmostra(dev, 2.5, out bytes, out boas, out total);
            }
            catch (Exception e) { erro = e.Message; return 0; }
            if (bytes > 0) { mudo = false; veioAlgo = true; }
            if (boas > 0) { exemplo = amostra; return baud; }
            // device sem hardware nao manda nada em velocidade nenhuma:
            // depois de duas tentativas mudas nao vale gastar as outras
            if (mudo && i >= 1) break;
        }
        return 0;
    }

    /// Varre as seriais e devolve a primeira com NMEA. E' o que permite
    /// SERIAL=auto: o nome do device deixa de ser problema de configuracao.
    static string AcharGpsAutomatico(out int baud)
    {
        baud = 0;
        foreach (string dev in CandidatosSeriais())
        {
            string exemplo, erro;
            bool veioAlgo;
            int b = SondaDevice(dev, out exemplo, out erro, out veioAlgo);
            if (b > 0)
            {
                baud = b;
                return dev;
            }
        }
        return null;
    }

    /// Varre todas as seriais do terminal e diz em qual delas ha NMEA.
    /// Existe porque adivinhar o nome do device foi, na pratica, o que mais
    /// atrapalhou: /dev/ttyS3 existe em quase toda maquina, mesmo sem placa.
    static int ProcurarGps()
    {
        Console.WriteLine("===============================================");
        Console.WriteLine(" PROCURANDO O GPS NAS SERIAIS - " + Callsign);
        Console.WriteLine("===============================================");
        var cands = CandidatosSeriais();
        if (cands.Count == 0)
        {
            Console.WriteLine("  Nenhum device serial neste terminal.");
            Console.WriteLine("  Confira com: ls -l /dev/tty*");
            return 1;
        }
        Console.WriteLine("  " + cands.Count + " device(s) para testar. "
                        + "Pode levar um minuto.");
        Console.WriteLine();

        var achados = new List<string>();
        foreach (string dev in cands)
        {
            Console.Write(("  " + dev).PadRight(34));
            string exemplo, erro;
            bool veioAlgo;
            int baud = SondaDevice(dev, out exemplo, out erro, out veioAlgo);
            if (baud > 0)
            {
                Console.WriteLine("*** GPS AQUI ***  " + baud + " baud");
                Console.WriteLine(new string(' ', 34) + exemplo);
                MostraPosicao(exemplo);
                achados.Add(dev + " a " + baud + " baud");
            }
            else if (erro != null)
                Console.WriteLine("erro (" + Curto(erro) + ")");
            else
                Console.WriteLine(veioAlgo
                    ? "abre e vem dado, mas nao e' NMEA valido"
                    : "abre, mas nao veio nada");
        }

        Console.WriteLine();
        Console.WriteLine("===============================================");
        if (achados.Count == 0)
        {
            Console.WriteLine(" Nao achei NMEA em serial nenhuma.");
            Console.WriteLine();
            Console.WriteLine(" Confira na mao qual device cospe as coordenadas:");
            Console.WriteLine("   cat /dev/ttyXXX      (Ctrl+C para sair)");
            Console.WriteLine(" e veja se o receptor esta ligado e a ceu aberto.");
            Console.WriteLine("===============================================");
            return 1;
        }
        Console.WriteLine(" ACHEI: " + string.Join(" | ", achados.ToArray()));
        Console.WriteLine();
        string primeiro = achados[0];
        int corte = primeiro.IndexOf(" a ");
        string devOk = primeiro.Substring(0, corte);
        string baudOk = primeiro.Substring(corte + 3).Replace(" baud", "");
        Console.WriteLine(" No /etc/ptxnav.conf:");
        Console.WriteLine("   SERIAL=" + devOk);
        Console.WriteLine("   BAUD=" + baudOk);
        Console.WriteLine("   systemctl restart ptxnav");
        Console.WriteLine();
        Console.WriteLine(" Com SERIAL=auto o servico procura sozinho no arranque, e");
        Console.WriteLine(" tambem cai na procura se o device configurado falhar.");
        Console.WriteLine("===============================================");
        return 0;
    }

    static void MostraPosicao(string linha)
    {
        try
        {
            string tipo = Nmea.Tipo(linha);
            string[] c = linha.Split(',');
            double la = 0, lo = 0;
            bool tem = false;
            if (tipo.Length == 5 && tipo.Substring(2) == "RMC" && c.Length > 6
                && c[2] == "A" && c[3].Length > 0)
            {
                la = Nmea.Graus(c[3], c[4]); lo = Nmea.Graus(c[5], c[6]); tem = true;
            }
            else if (tipo.Length == 5 && tipo.Substring(2) == "GGA" && c.Length > 6
                     && c[2].Length > 0 && Nmea.Num(c[6]) > 0)
            {
                la = Nmea.Graus(c[2], c[3]); lo = Nmea.Graus(c[4], c[5]); tem = true;
            }
            if (tem)
                Console.WriteLine(new string(' ', 34) + "posicao: " + N(la) + ", " + N(lo));
            else
                Console.WriteLine(new string(' ', 34) + "(NMEA valido, mas ainda sem fix)");
        }
        catch { }
    }

    static void LacoDeLeitura(Stream s)
    {
        var buf = new byte[512];
        var linha = new StringBuilder(128);
        while (true)
        {
            int n = s.Read(buf, 0, buf.Length);
            if (n <= 0) throw new IOException("fim do device");
            UltimoByte = DateTime.UtcNow;
            for (int i = 0; i < n; i++)
            {
                char c = (char)buf[i];
                if (c == '\n' || c == '\r')
                {
                    if (linha.Length > 0) { Processa(linha.ToString()); linha.Length = 0; }
                }
                else if (linha.Length < 120) linha.Append(c);
                else linha.Length = 0;          // lixo: descarta e ressincroniza
            }
        }
    }

    /// Fecha a serial quando ela emudece; a leitura estoura e o laco reconecta.
    static void VigiaSerial()
    {
        while (true)
        {
            Thread.Sleep(2000);
            var s = SerialAberta;
            if (s == null) continue;
            double calado = (DateTime.UtcNow - UltimoByte).TotalSeconds;
            if (calado > 15)
            {
                Log.E("gps", "serial calada ha " + (int)calado + " s; reconectando");
                try { s.Close(); } catch { }
                SerialAberta = null;
            }
        }
    }

    static void Processa(string linha)
    {
        if (!Nmea.ChecksumOk(linha))
        {
            Interlocked.Increment(ref LinhasRuins);
            return;
        }
        string tipo = Nmea.Tipo(linha);
        if (!Nmea.TalkerAceito(tipo)) return;
        Interlocked.Increment(ref LinhasBoas);

        string corpo = tipo.Substring(2);
        if (corpo == "RMC") ParseRmc(linha);
        else if (corpo == "GGA") ParseGga(linha);
        else return;

        if (NmeaTcp > 0) Reemite(linha);
    }

    static void ParseRmc(string linha)
    {
        string[] c = linha.Split(',');
        if (c.Length < 9) return;
        bool valido = c[2] == "A";
        lock (Trava)
        {
            if (valido && c[3].Length > 0)
            {
                Atual.Lat = Nmea.Graus(c[3], c[4]);
                Atual.Lon = Nmea.Graus(c[5], c[6]);
            }
            Atual.SogNos = Nmea.Num(c[7]);
            if (c[8].Length > 0) Atual.CogGraus = Nmea.Num(c[8]);
            Atual.Valido = valido;
            Atual.Quando = DateTime.UtcNow;
        }
    }

    static void ParseGga(string linha)
    {
        string[] c = linha.Split(',');
        if (c.Length < 10) return;
        int q = (int)Nmea.Num(c[6]);
        lock (Trava)
        {
            if (q > 0 && c[2].Length > 0)
            {
                Atual.Lat = Nmea.Graus(c[2], c[3]);
                Atual.Lon = Nmea.Graus(c[4], c[5]);
                Atual.Altitude = Nmea.Num(c[9]);
                Atual.Valido = true;
            }
            else if (q == 0) Atual.Valido = false;
            Atual.Qualidade = q;
            Atual.Quando = DateTime.UtcNow;
        }
    }

    /// Copia do fix com a idade ja' calculada. TemFix junta as tres condicoes:
    /// sentenca recebida, receptor dizendo que o fix presta, e sentenca recente.
    static void LerFix(out Fix f, out double idade, out bool temFix)
    {
        lock (Trava)
        {
            f = new Fix
            {
                Lat = Atual.Lat, Lon = Atual.Lon, CogGraus = Atual.CogGraus,
                SogNos = Atual.SogNos, Altitude = Atual.Altitude,
                Qualidade = Atual.Qualidade, Valido = Atual.Valido, Quando = Atual.Quando
            };
        }
        idade = f.Quando == DateTime.MinValue ? -1 : (DateTime.UtcNow - f.Quando).TotalSeconds;
        temFix = f.Valido && idade >= 0 && idade <= SegSemFix;
    }

    static int DescobrirBaud(string dev)
    {
        foreach (int b in BaudsCandidatos)
        {
            Log.E("gps", "testando " + b + " baud em " + dev);
            if (!ConfiguraStty(dev, b) && b != BaudsCandidatos[0])
                Log.E("gps", "stty falhou em " + b + "; testando assim mesmo");
            int boas = 0;
            try
            {
                using (var s = Abrir(dev))
                {
                    var buf = new byte[256];
                    var linha = new StringBuilder(128);
                    DateTime fim = DateTime.UtcNow.AddSeconds(4);
                    SerialAberta = s; UltimoByte = DateTime.UtcNow;
                    while (DateTime.UtcNow < fim && boas < 2)
                    {
                        int n = s.Read(buf, 0, buf.Length);
                        if (n <= 0) break;
                        UltimoByte = DateTime.UtcNow;
                        for (int i = 0; i < n; i++)
                        {
                            char c = (char)buf[i];
                            if (c == '\n' || c == '\r')
                            {
                                if (linha.Length > 0)
                                {
                                    if (Nmea.ChecksumOk(linha.ToString())) boas++;
                                    linha.Length = 0;
                                }
                            }
                            else if (linha.Length < 120) linha.Append(c);
                            else linha.Length = 0;
                        }
                    }
                }
            }
            catch (Exception e)
            {
                Log.E("gps", ExplicaFalhaDeAbertura(dev, e.Message));
                break;      // se nao abre, nao abre em velocidade nenhuma
            }
            finally { SerialAberta = null; }

            if (boas >= 2) { Log.E("gps", "velocidade encontrada: " + b + " baud"); return b; }
        }
        return 0;
    }

    /// Modo raw. Se nao houver stty no terminal, segue sem configurar: muitos
    /// receptores ja' vem no padrao certo.
    static bool ConfiguraStty(string dev, int baud)
    {
        return ConfiguraStty(dev, baud, 0);
    }

    /// timeoutDecimos > 0 poe o device em "min 0 time N": a leitura devolve 0
    /// depois de N decimos de segundo sem dado, em vez de travar para sempre.
    /// E' o que permite varrer devices silenciosos sem pendurar o programa.
    /// Em operacao normal usamos 0 (bloqueante), que gasta menos CPU.
    static bool ConfiguraStty(string dev, int baud, int timeoutDecimos)
    {
        try
        {
            string extra = timeoutDecimos > 0
                ? " min 0 time " + timeoutDecimos
                : " min 1 time 0";
            var psi = new ProcessStartInfo("stty",
                "-F " + dev + " " + baud + " raw -echo -echoe -echok clocal -crtscts" + extra);
            psi.UseShellExecute = false;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            using (var p = Process.Start(psi))
            {
                if (!p.WaitForExit(4000)) { try { p.Kill(); } catch { } return false; }
                return p.ExitCode == 0;
            }
        }
        catch (Exception e)
        {
            Log.E("gps", "sem stty (" + e.Message + "); lendo o device como esta'");
            return false;
        }
    }

    static void ServirNmea()
    {
        TcpListener l = null;
        while (true)
        {
            try
            {
                l = new TcpListener(IPAddress.Any, NmeaTcp);
                l.Start();
                Log.E("nmea-tcp", "reemitindo NMEA na porta " + NmeaTcp);
                while (true)
                {
                    TcpClient c = l.AcceptTcpClient();
                    lock (Ouvintes) Ouvintes.Add(c);
                    Log.E("nmea-tcp", "ouvinte conectado (" + Ouvintes.Count + ")");
                }
            }
            catch (Exception e)
            {
                Log.E("nmea-tcp", e.Message);
                try { if (l != null) l.Stop(); } catch { }
                Thread.Sleep(5000);
            }
        }
    }

    static void Reemite(string linha)
    {
        byte[] b = Encoding.ASCII.GetBytes(linha + "\r\n");
        lock (Ouvintes)
        {
            for (int i = Ouvintes.Count - 1; i >= 0; i--)
            {
                try { Ouvintes[i].GetStream().Write(b, 0, b.Length); }
                catch
                {
                    try { Ouvintes[i].Close(); } catch { }
                    Ouvintes.RemoveAt(i);
                }
            }
        }
    }

    class Resposta { public int Status; public string Corpo = ""; public string ETag = ""; }

    static Resposta Baixar(string url, string etag)
    {
        var r = (HttpWebRequest)WebRequest.Create(url);
        r.Timeout = 15000; r.ReadWriteTimeout = 20000;
        r.UserAgent = "PtxNav/" + Callsign;
        if (!string.IsNullOrEmpty(etag)) r.Headers["If-None-Match"] = etag;
        try
        {
            using (var resp = (HttpWebResponse)r.GetResponse())
            using (var sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                return new Resposta { Status = (int)resp.StatusCode, Corpo = sr.ReadToEnd(),
                                      ETag = resp.Headers["ETag"] ?? "" };
        }
        catch (WebException we)
        {
            // 304 chega aqui como excecao: nao e' erro, e' "nada mudou"
            var hr = we.Response as HttpWebResponse;
            if (hr != null)
            {
                int st = (int)hr.StatusCode;
                string corpo = "";
                try
                {
                    using (var sr = new StreamReader(hr.GetResponseStream(), Encoding.UTF8))
                        corpo = sr.ReadToEnd();
                }
                catch { }
                string et = hr.Headers["ETag"] ?? "";
                hr.Close();
                if (st == 304) return new Resposta { Status = 304, ETag = et };
                return new Resposta { Status = st, Corpo = corpo, ETag = et };
            }
            throw;
        }
    }

    static void Sincroniza()
    {
        while (true)
        {
            try
            {
                var v = Baixar(Servidor + "/api/versao", HashLocal);
                if (v.Status == 304)
                {
                    EstadoServidor = "online";
                    QuandoSinc = Agora();
                    Log.E("sinc", "sem novidade (304)");
                }
                else if (v.Status == 200)
                {
                    string h = Campo(v.Corpo, "hash");
                    if (h != "" && h != HashLocal)
                    {
                        Log.E("sinc", "dados novos no servidor (" + h + "); baixando");
                        var malha  = Baixar(Servidor + "/api/malha", null);
                        var locais = Baixar(Servidor + "/api/locais", null);
                        var areas  = Baixar(Servidor + "/api/areas", null);
                        // so' troca o cache se as tres vieram inteiras
                        if (malha.Status == 200 && locais.Status == 200 &&
                            malha.Corpo.Length > 100 && locais.Corpo.Length > 100)
                        {
                            Cache.Grava("malha.json", malha.Corpo);
                            Cache.Grava("locais.json", locais.Corpo);
                            if (areas.Status == 200 && areas.Corpo.Length > 20)
                                Cache.Grava("areas.json", areas.Corpo);
                            Cache.Grava("hash.txt", h);
                            HashLocal = h;
                            Log.E("sinc", "cache atualizado (" +
                                (malha.Corpo.Length + locais.Corpo.Length) / 1024 + " KB)");
                        }
                        else Log.E("sinc", "download incompleto; mantendo o cache antigo");
                    }
                    EstadoServidor = "online";
                    QuandoSinc = Agora();
                }
                else EstadoServidor = "resposta inesperada: HTTP " + v.Status;
            }
            catch (Exception e)
            {
                EstadoServidor = "offline: " + e.Message;
                Log.E("sinc", "sem servidor: " + e.Message);
            }
            Thread.Sleep(SincSeg * 1000);
        }
    }

    /// Extrator minimo de campo texto: nao vale um parser inteiro so' para o hash.
    static string Campo(string json, string campo)
    {
        string chave = "\"" + campo + "\"";
        int i = json.IndexOf(chave); if (i < 0) return "";
        i = json.IndexOf(':', i); if (i < 0) return "";
        int a = json.IndexOf('"', i); if (a < 0) return "";
        int b = json.IndexOf('"', a + 1); if (b < 0) return "";
        return json.Substring(a + 1, b - a - 1);
    }

    static string Agora()
    {
        return DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture);
    }

    static void Servir()
    {
        var l = new HttpListener();
        string[] tentativas = { "http://*:" + PortaLocal + "/",
                                "http://127.0.0.1:" + PortaLocal + "/" };
        bool subiu = false;
        foreach (string p in tentativas)
        {
            try
            {
                l = new HttpListener();
                l.Prefixes.Add(p);
                l.Start();
                Log.E("web", "interface em " + p.Replace("*", "<ip-do-ptx>"));
                subiu = true;
                break;
            }
            catch (Exception e) { Log.E("web", "nao consegui abrir " + p + ": " + e.Message); }
        }
        if (!subiu) { Log.E("web", "sem porta para a interface; encerrando"); return; }

        while (true)
        {
            try
            {
                var ctx = l.GetContext();
                ThreadPool.QueueUserWorkItem(delegate { Atende(ctx); });
            }
            catch (Exception e) { Log.E("web", e.Message); }
        }
    }

    static void Atende(HttpListenerContext ctx)
    {
        try
        {
            string caminho = ctx.Request.Url.AbsolutePath;
            string tipo = "application/json; charset=utf-8";
            string corpo;

            if (ctx.Request.HttpMethod == "POST")
            {
                corpo = caminho == "/config" ? MudaConfig(ctx)
                      : "{\"ok\":false,\"erro\":\"nao encontrado\"}";
            }
            else if (caminho == "/" || caminho == "/index.html")
            {
                tipo = "text/html; charset=utf-8"; corpo = Pagina.HTML;
            }
            else if (caminho == "/malha")  corpo = Cache.Le("malha.json");
            else if (caminho == "/locais") corpo = Cache.Le("locais.json");
            else if (caminho == "/areas")  corpo = Cache.Le("areas.json");
            else if (caminho == "/frota")  corpo = PegaFrota();
            else if (caminho == "/desmontes") corpo = PegaDesmontes();
            else if (caminho == "/pos")    corpo = JsonPos();
            else if (caminho == "/estado") corpo = JsonEstado();
            else if (caminho == "/rota")   corpo = PedeRota(ctx);
            else { ctx.Response.StatusCode = 404; corpo = "{\"erro\":\"nao encontrado\"}"; }

            if (string.IsNullOrEmpty(corpo))
                corpo = "{\"erro\":\"sem dados em cache; aguarde a sincronizacao\"}";

            byte[] b = Encoding.UTF8.GetBytes(corpo);
            ctx.Response.ContentType = tipo;
            ctx.Response.ContentLength64 = b.Length;
            ctx.Response.AddHeader("Cache-Control", "no-store");
            ctx.Response.OutputStream.Write(b, 0, b.Length);
        }
        catch (Exception e) { Log.E("web", "erro atendendo: " + e.Message); }
        finally { try { ctx.Response.Close(); } catch { } }
    }

    static string JsonPos()
    {
        Fix f; double idade; bool temFix;
        LerFix(out f, out idade, out temFix);
        bool parado = f.SogKmh <= LimiteParadoKmh;
        var sb = new StringBuilder();
        sb.Append("{\"callsign\":\"").Append(Esc(Callsign)).Append("\"");
        sb.Append(",\"lat\":").Append(N(f.Lat));
        sb.Append(",\"lon\":").Append(N(f.Lon));
        sb.Append(",\"cog\":").Append(N(f.CogGraus));
        sb.Append(",\"sog_kmh\":").Append(N(f.SogKmh));
        sb.Append(",\"altitude\":").Append(N(f.Altitude));
        sb.Append(",\"qualidade\":").Append(f.Qualidade);
        sb.Append(",\"idade_s\":").Append(N(idade));
        sb.Append(",\"tem_fix\":").Append(temFix ? "true" : "false");
        sb.Append(",\"parado\":").Append(parado ? "true" : "false");
        sb.Append(",\"limite_parado_kmh\":").Append(N(LimiteParadoKmh));
        sb.Append("}");
        return sb.ToString();
    }

    /// Como a posicao esta chegando. A interface e' a mesma no PTX e no
    /// tablet Android, e cada um preenche isto do seu jeito.
    static string FonteDaPosicao()
    {
        string dev = SerialEmUso.Length > 0 ? SerialEmUso : Serial;
        if (BaudEmUso > 0) return dev + " @ " + BaudEmUso + " baud";
        return dev;
    }

    static string JsonEstado()
    {
        var sb = new StringBuilder();
        sb.Append("{\"servidor\":\"").Append(Esc(EstadoServidor)).Append("\"");
        sb.Append(",\"gps\":\"").Append(Esc(EstadoGps)).Append("\"");
        sb.Append(",\"servidor_url\":\"").Append(Esc(Servidor)).Append("\"");
        sb.Append(",\"fonte\":\"")
          .Append(Esc(FonteDaPosicao())).Append("\"");
        sb.Append(",\"serial\":\"")
          .Append(Esc(SerialEmUso.Length > 0 ? SerialEmUso : Serial)).Append("\"");
        sb.Append(",\"serial_configurada\":\"").Append(Esc(Serial)).Append("\"");
        sb.Append(",\"baud\":").Append(BaudEmUso);
        sb.Append(",\"hash\":\"").Append(Esc(HashLocal)).Append("\"");
        sb.Append(",\"sinc\":\"").Append(Esc(QuandoSinc)).Append("\"");
        sb.Append(",\"linhas_boas\":").Append(Interlocked.Read(ref LinhasBoas));
        sb.Append(",\"linhas_ruins\":").Append(Interlocked.Read(ref LinhasRuins));
        sb.Append(",\"tem_cache\":").Append(Cache.Tem("malha.json") ? "true" : "false");
        sb.Append("}");
        return sb.ToString();
    }

    static string PedeRota(HttpListenerContext ctx)
    {
        Fix f; double idade; bool temFix;
        LerFix(out f, out idade, out temFix);
        string destino = ctx.Request.QueryString["para"] ?? "";
        string equipamento = ctx.Request.QueryString["equip"] ?? "";
        if (destino.Length == 0 && equipamento.Length == 0)
            return "{\"ok\":false,\"erro\":\"escolha um destino\"}";
        if (!temFix)
            return "{\"ok\":false,\"erro\":\"sem posicao GPS valida\"," +
                   "\"detalhe\":\"o receptor nao esta' dando fix; a rota precisa saber onde voce esta'\"}";

        string url = equipamento.Length > 0
            ? Servidor + "/api/rota?de_lat=" + N(f.Lat) + "&de_lon=" + N(f.Lon) +
              "&para_equip=" + Uri.EscapeDataString(equipamento)
            : Servidor + "/api/rota?de_lat=" + N(f.Lat) + "&de_lon=" + N(f.Lon) +
              "&para=" + Uri.EscapeDataString(destino);
        try
        {
            var r = Baixar(url, null);
            if (r.Status == 200 && r.Corpo.Length > 10)
            {
                // rota ate equipamento nao vai para o cache: o alvo anda, e
                // uma rota velha para maquina em movimento engana o operador
                if (equipamento.Length == 0) Cache.Grava("ultima_rota.json", r.Corpo);
                return r.Corpo;
            }
            if (r.Corpo.Length > 10) return r.Corpo;   // erro explicado pelo servidor
            return "{\"ok\":false,\"erro\":\"servidor respondeu HTTP " + r.Status + "\"}";
        }
        catch (Exception e)
        {
            Log.E("rota", "servidor fora: " + e.Message);
            // A pagina calcula a rota sozinha pela malha em cache. Este campo
            // avisa que e' para fazer isso.
            return "{\"ok\":false,\"offline\":true,\"erro\":\"servidor de rotas indisponivel\"," +
                   "\"detalhe\":\"" + Esc(e.Message) + "\"}";
        }
    }

    /// Repassa a frota do servidor. Se o servidor cair, devolve a ultima
    /// conhecida marcada com a idade — posicao velha de maquina que anda tem
    /// que aparecer como velha, nunca como atual.
    static string PegaFrota()
    {
        try
        {
            var r = Baixar(Servidor + "/api/equipamentos", null);
            if (r.Status == 200 && r.Corpo.Length > 10)
            {
                lock (TravaFrota) { FrotaJson = r.Corpo; FrotaQuando = DateTime.UtcNow; }
                return r.Corpo;
            }
            if (r.Corpo.Length > 10) return r.Corpo;
        }
        catch (Exception e)
        {
            Log.E("frota", "sem frota do servidor: " + e.Message);
        }
        string ultima; double idade;
        lock (TravaFrota)
        {
            ultima = FrotaJson;
            idade = FrotaQuando == DateTime.MinValue ? -1
                  : (DateTime.UtcNow - FrotaQuando).TotalSeconds;
        }
        if (ultima.Length > 10)
            return ultima.Insert(1, "\"do_cache\":true,\"idade_cache_s\":" +
                                    N(Math.Round(idade, 0)) + ",");
        return "{\"ok\":false,\"total\":0,\"equipamentos\":[]," +
               "\"erro\":\"servidor indisponivel e sem frota em cache\"}";
    }

    /// O menu da interface muda o endereco do servidor sem ninguem precisar
    /// abrir terminal. Fica gravado nas duas camadas do aufs, entao sobrevive
    /// ao reboot sem mexer no /etc/ptxnav.conf.
    static string MudaConfig(HttpListenerContext ctx)
    {
        string corpo;
        try
        {
            using (var sr = new StreamReader(ctx.Request.InputStream, Encoding.UTF8))
                corpo = sr.ReadToEnd();
        }
        catch (Exception e)
        {
            return "{\"ok\":false,\"erro\":\"" + Esc(e.Message) + "\"}";
        }

        // o corpo e' a fonte no PTX; a query existe porque o WebView do
        // Android nao entrega corpo de POST, e a pagina e' a mesma nos dois
        string url = (Campo(corpo, "servidor") ?? "").Trim().TrimEnd('/');
        if (url.Length == 0)
            url = (ctx.Request.QueryString["servidor"] ?? "").Trim().TrimEnd('/');
        if (url.Length == 0)
            return "{\"ok\":false,\"erro\":\"endereco vazio\"}";
        if (!url.StartsWith("http://") && !url.StartsWith("https://"))
            url = "http://" + url;
        Uri teste;
        if (!Uri.TryCreate(url, UriKind.Absolute, out teste))
            return "{\"ok\":false,\"erro\":\"endereco invalido\"}";

        Servidor = url;
        Cache.Grava("config_local.json", "{\"servidor\":\"" + Esc(url) + "\"}");
        Log.E("config", "servidor mudado pela interface para " + url);
        HashLocal = "";                 // forca baixar a malha do servidor novo
        return "{\"ok\":true,\"servidor\":\"" + Esc(url) + "\"}";
    }

    /// Repassa as areas de desmonte. Diferente da frota, estas vao para o
    /// cache em disco: mudam uma vez por semana e sao informacao de seguranca
    /// — nao podem sumir da tela porque o servidor caiu.
    static string PegaDesmontes()
    {
        try
        {
            var r = Baixar(Servidor + "/api/desmontes", null);
            if (r.Status == 200 && r.Corpo.Length > 10)
            {
                lock (TravaDesmonte) DesmonteJson = r.Corpo;
                Cache.Grava("desmontes.json", r.Corpo);
                return r.Corpo;
            }
            if (r.Corpo.Length > 10) return r.Corpo;
        }
        catch (Exception e)
        {
            Log.E("desmonte", "sem desmontes do servidor: " + e.Message);
        }
        string ultima;
        lock (TravaDesmonte) ultima = DesmonteJson;
        if (ultima.Length <= 10) ultima = Cache.Le("desmontes.json");
        if (ultima != null && ultima.Length > 10)
            return ultima.Insert(1, "\"do_cache\":true,");
        return "{\"ok\":false,\"total\":0,\"desmontes\":[]," +
               "\"erro\":\"servidor indisponivel e sem desmonte em cache\"}";
    }

    static string Esc(string s)
    {
        if (s == null) return "";
        return s.Replace("\\", "\\\\").Replace("\"", "\\\"")
                .Replace("\n", " ").Replace("\r", " ").Replace("\t", " ");
    }

    static string N(double v)
    {
        if (double.IsNaN(v) || double.IsInfinity(v)) return "0";
        return v.ToString("0.######", CultureInfo.InvariantCulture);
    }
}
