package br.com.mina.navegacao

import android.content.Context
import android.util.Log
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.atomic.AtomicReference

/**
 * Conversa com o servidor de rotas e guarda o resultado em disco.
 *
 * Mesma logica do cliente do PTX: baixa so' quando o hash muda (ETag), grava
 * de forma atomica e serve do cache quando o servidor nao responde. O tablet
 * anda pela cava e perde a rede o tempo todo; ficar sem mapa por isso nao e'
 * aceitavel.
 */
class Sincronizacao(ctx: Context, private val config: Config) {

    companion object {
        private const val TAG = "Sinc"
        // quanto tempo vale uma sincronizacao boa antes de perguntar de novo
        private const val ENTRE_SINCS_MS = 300_000L
        private const val TIMEOUT_MS = 15_000
    }

    private val pasta = File(ctx.filesDir, "cache").apply { mkdirs() }
    private val pastaFoto = File(ctx.filesDir, "foto").apply { mkdirs() }

    @Volatile var estadoServidor = "nunca contatado"; private set
    @Volatile var quandoSinc = "nunca"; private set
    @Volatile var hashLocal = ""; private set

    // frota e' dado vivo: fica so' em memoria, como no PTX
    private val frota = AtomicReference("")
    @Volatile private var frotaQuandoMs = 0L

    init {
        hashLocal = leArquivo("hash.txt").trim()
    }

    @Volatile private var ultimoOkMs = 0L

    private fun anotaOk() {
        estadoServidor = "online"
        quandoSinc = agora()
        ultimoOkMs = System.currentTimeMillis()
    }

    class Resposta(val status: Int, val corpo: String, val etag: String)

    private fun baixa(caminho: String, etag: String? = null): Resposta {
        val url = URL(config.servidor + caminho)
        val c = url.openConnection() as HttpURLConnection
        try {
            c.connectTimeout = TIMEOUT_MS
            c.readTimeout = TIMEOUT_MS
            c.setRequestProperty("User-Agent", "NavegacaoMina/${config.callsign}")
            if (!etag.isNullOrEmpty()) c.setRequestProperty("If-None-Match", etag)
            val status = c.responseCode
            val fluxo = if (status in 200..299) c.inputStream else c.errorStream
            val corpo = fluxo?.bufferedReader()?.use { it.readText() } ?: ""
            return Resposta(status, corpo, c.getHeaderField("ETag") ?: "")
        } finally {
            c.disconnect()
        }
    }

    /**
     * Uma rodada de sincronizacao. Devolve true se trocou o cache.
     *
     * O laco chama isto de minuto em minuto, mas quando o servidor esta' no
     * ar so' ha conversa de verdade a cada [ENTRE_SINCS_MS]. A frequencia
     * existe para reencontrar o servidor depressa depois de uma queda: antes,
     * uma falha no arranque — o Wi-Fi do tablet ainda subindo, por exemplo —
     * deixava "servidor fora do ar" na tela por cinco minutos, com a frota
     * chegando normalmente ao lado.
     */
    fun sincroniza(): Boolean {
        if (estadoServidor == "online" &&
            System.currentTimeMillis() - ultimoOkMs < ENTRE_SINCS_MS) {
            return false
        }
        try {
            val versao = baixa("/api/versao", hashLocal)
            if (versao.status == 304) {
                anotaOk()
                return false
            }
            if (versao.status != 200) {
                estadoServidor = "resposta inesperada: HTTP ${versao.status}"
                return false
            }
            val h = Json.campo(versao.corpo, "hash")
            if (h.isNotEmpty() && h != hashLocal) {
                val malha = baixa("/api/malha")
                val locais = baixa("/api/locais")
                val areas = baixa("/api/areas")
                // so' troca o cache se as duas essenciais vieram inteiras
                if (malha.status == 200 && locais.status == 200 &&
                    malha.corpo.length > 100 && locais.corpo.length > 100) {
                    grava("malha.json", malha.corpo)
                    grava("locais.json", locais.corpo)
                    if (areas.status == 200 && areas.corpo.length > 20)
                        grava("areas.json", areas.corpo)
                    grava("hash.txt", h)
                    hashLocal = h
                    Log.i(TAG, "cache atualizado ($h)")
                    anotaOk()
                    return true
                }
                Log.w(TAG, "download incompleto; mantendo o cache antigo")
            }
            anotaOk()
        } catch (e: Exception) {
            estadoServidor = "offline: ${e.message}"
        }
        return false
    }

    /**
     * Ladrilho da foto aerea: do disco quando ja' veio, do servidor na
     * primeira vez.
     *
     * Ladrilho nao muda depois de gerado — o servidor manda guardar para
     * sempre — entao vale gravar e nunca mais perguntar. Numa rede de mina
     * essa e' a diferenca entre a foto abrir na hora e a tela ficar cinza
     * esperando o Wi-Fi.
     */
    fun foto(caminho: String): ByteArray? {
        if (!caminhoSeguro(caminho)) return null
        val arq = File(pastaFoto, caminho.replace('/', '_'))
        // o manifesto muda quando geram uma foto nova; ladrilho, nunca
        if (caminho.endsWith(".json")) {
            val novo = baixaBinario("/foto/$caminho")
            if (novo != null) {
                try {
                    arq.writeBytes(novo)
                } catch (e: Exception) {
                    Log.w(TAG, "nao consegui guardar o manifesto", e)
                }
                return novo
            }
            return if (arq.isFile) arq.readBytes() else null
        }
        if (arq.isFile) {
            try {
                return arq.readBytes()
            } catch (e: Exception) {
                Log.w(TAG, "ladrilho ilegivel: $caminho", e)
            }
        }
        val bytes = baixaBinario("/foto/$caminho") ?: return null
        try {
            val tmp = File(arq.parentFile, arq.name + ".tmp")
            tmp.writeBytes(bytes)
            tmp.renameTo(arq)
        } catch (e: Exception) {
            Log.w(TAG, "nao consegui guardar $caminho", e)
        }
        return bytes
    }

    /** So' o que o mosaico gera: z/x/y.jpg e o manifesto. Nada de subir pasta. */
    private fun caminhoSeguro(caminho: String) =
        caminho.isNotEmpty() && !caminho.contains("..") &&
        caminho.all { it.isLetterOrDigit() || it == '/' || it == '.' || it == '_' }

    private fun baixaBinario(caminho: String): ByteArray? {
        return try {
            val c = URL(config.servidor + caminho).openConnection() as HttpURLConnection
            try {
                c.connectTimeout = TIMEOUT_MS
                c.readTimeout = TIMEOUT_MS
                if (c.responseCode !in 200..299) return null
                c.inputStream.use { it.readBytes() }
            } finally {
                c.disconnect()
            }
        } catch (e: Exception) {
            null
        }
    }

    fun malha() = leArquivo("malha.json")
    fun locais() = leArquivo("locais.json")
    fun areas() = leArquivo("areas.json")
    fun temCache() = File(pasta, "malha.json").exists()

    /**
     * Frota ao vivo. Se o servidor cair, devolve a ultima conhecida com a
     * idade marcada: maquina que anda com posicao velha nao pode parecer atual.
     */
    fun frotaAoVivo(): String {
        try {
            val r = baixa("/api/equipamentos")
            if (r.status == 200 && r.corpo.length > 10) {
                frota.set(r.corpo)
                frotaQuandoMs = System.currentTimeMillis()
                return r.corpo
            }
            if (r.corpo.length > 10) return r.corpo
        } catch (e: Exception) {
            Log.d(TAG, "sem frota: ${e.message}")
        }
        val ultima = frota.get()
        if (ultima.length > 10) {
            val idade = (System.currentTimeMillis() - frotaQuandoMs) / 1000
            return ultima.replaceFirst("{", """{"do_cache":true,"idade_cache_s":$idade,""")
        }
        return """{"ok":false,"total":0,"equipamentos":[],""" +
               """"erro":"servidor indisponivel e sem frota em cache"}"""
    }

    /**
     * Areas de desmonte. Diferente da frota, vao para o disco: mudam uma vez
     * por semana e sao informacao de seguranca.
     */
    fun desmontes(): String {
        try {
            val r = baixa("/api/desmontes")
            if (r.status == 200 && r.corpo.length > 10) {
                grava("desmontes.json", r.corpo)
                return r.corpo
            }
            if (r.corpo.length > 10) return r.corpo
        } catch (e: Exception) {
            Log.d(TAG, "sem desmontes: ${e.message}")
        }
        val guardado = leArquivo("desmontes.json")
        if (guardado.length > 10)
            return guardado.replaceFirst("{", """{"do_cache":true,""")
        return """{"ok":false,"total":0,"desmontes":[],""" +
               """"erro":"servidor indisponivel e sem desmonte em cache"}"""
    }

    fun rota(caminho: String): String {
        return try {
            val r = baixa(caminho)
            if (r.corpo.length > 10) r.corpo
            else Json.erro("servidor respondeu HTTP ${r.status}")
        } catch (e: Exception) {
            """{"ok":false,"offline":true,"erro":"servidor de rotas indisponivel",""" +
            """"detalhe":"${Json.escapa(e.message)}"}"""
        }
    }

    fun estadoJson(fontePosicao: String, ultimaFalha: String? = null,
                   versaoApp: String = ""): String {
        // so' a primeira linha da pilha: e' onde esta' o tipo do erro, e o
        // menu nao e' lugar para vinte linhas de stack
        val resumo = ultimaFalha?.lineSequence()
            ?.filter { it.isNotBlank() }
            ?.take(2)?.joinToString(" — ")?.take(200) ?: ""
        return """{"servidor":"${Json.escapa(estadoServidor)}",""" +
               """"servidor_url":"${Json.escapa(config.servidor)}",""" +
               """"fonte":"${Json.escapa(fontePosicao)}",""" +
               """"ultima_falha":"${Json.escapa(resumo)}",""" +
               """"versao_app":"${Json.escapa(versaoApp)}",""" +
               """"hash":"${Json.escapa(hashLocal)}","sinc":"${Json.escapa(quandoSinc)}",""" +
               """"tem_cache":${temCache()}}"""
    }

    /** Grava atomico: nunca deixa arquivo pela metade se a bateria acabar. */
    private fun grava(nome: String, texto: String) {
        try {
            val tmp = File(pasta, "$nome.tmp")
            tmp.writeText(texto)
            val destino = File(pasta, nome)
            if (destino.exists()) destino.delete()
            if (!tmp.renameTo(destino)) {
                destino.writeText(texto)
                tmp.delete()
            }
        } catch (e: Exception) {
            Log.w(TAG, "falhou gravar $nome", e)
        }
    }

    private fun leArquivo(nome: String): String = try {
        val f = File(pasta, nome)
        if (f.exists()) f.readText() else ""
    } catch (e: Exception) {
        Log.w(TAG, "falhou ler $nome", e); ""
    }

    /** Semeia o cache com o que veio dentro do APK, no primeiro uso. */
    fun semeiaSePreciso(ctx: Context) {
        if (temCache()) return
        for (nome in listOf("malha.json", "locais.json", "areas.json", "hash.txt")) {
            try {
                ctx.assets.open("semente/$nome").use { entrada ->
                    File(pasta, nome).outputStream().use { entrada.copyTo(it) }
                }
            } catch (_: Exception) {
                // semente e' opcional: sem ela o app espera a primeira sincronizacao
            }
        }
        hashLocal = leArquivo("hash.txt").trim()
        if (temCache()) Log.i(TAG, "cache semeado do APK ($hashLocal)")
    }

    private fun agora(): String =
        java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US)
            .format(java.util.Date())
}
