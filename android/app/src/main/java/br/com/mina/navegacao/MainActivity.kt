package br.com.mina.navegacao

import android.Manifest
import android.annotation.SuppressLint
import android.content.pm.ActivityInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.ScrollView
import android.widget.TextView
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.app.Activity
import androidx.webkit.WebViewAssetLoader
import java.io.ByteArrayInputStream
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * A aplicacao inteira e' a interface.html do PTX rodando num WebView.
 *
 * O WebView do Android e' Chromium, o mesmo motor onde a interface ja' esta'
 * testada — entao a tela e o comportamento sao os mesmos do terminal. O que
 * muda e' de onde vem a posicao: aqui, do GPS do proprio tablet.
 */
class MainActivity : Activity() {

    private var montou = false
    private var falhaAnterior: String? = null
    private lateinit var web: WebView
    private lateinit var config: Config
    private lateinit var posicao: Posicao
    private lateinit var sinc: Sincronizacao
    private lateinit var api: Api
    private var voz: Voz? = null

    private val agenda = Executors.newSingleThreadScheduledExecutor()

    companion object {
        private val MODOS = setOf("paisagem", "retrato", "auto")
        private const val PEDIDO_LOCALIZACAO = 1
        // dominio virtual do WebViewAssetLoader; nao vai para a rede
        private const val BASE = "https://appassets.androidplatform.net"
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(estado: Bundle?) {
        super.onCreate(estado)

        // A falha da execucao anterior NAO trava o arranque. Segurar a tela com
        // um relatorio antigo faz parecer que o app continua quebrado depois de
        // corrigido — e nao da' para distinguir "quebrou agora" de "quebrou da
        // outra vez". Ela vai para o menu, em Estado, com a data.
        falhaAnterior = Falhas.ultima(this)
        Falhas.limpa(this)

        try {
            monta()
        } catch (e: Throwable) {
            // Fechar calado deixa quem esta' com o tablet na mao sem nada para
            // contar. Melhor a tela feia com o motivo escrito.
            Falhas.grava(this, e)
            mostraFalha(Falhas.comoTexto(e))
        }
    }

    /**
     * Versao e data de instalacao: e' o que diz qual APK esta' no aparelho.
     *
     * Vem do PackageManager, em tempo de execucao. A primeira versao disto
     * calculava a data no build.gradle.kts com java.time — e em script Gradle
     * Kotlin "java" resolve para a extensao do plugin Java, nao para o pacote:
     * o sync quebrava e nenhum APK novo saia. Aqui nao ha esse risco.
     */
    private fun carimbo(): String = try {
        val info = packageManager.getPackageInfo(packageName, 0)
        val quando = java.text.SimpleDateFormat("dd/MM HH:mm", java.util.Locale.US)
            .format(java.util.Date(info.lastUpdateTime))
        "versao ${info.versionName} · instalado $quando"
    } catch (e: Exception) {
        "versao desconhecida"
    }

    private fun mostraFalha(texto: String) {
        val corpo = TextView(this).apply {
            setTextColor(0xFFFFCCCC.toInt())
            setBackgroundColor(0xFF101010.toInt())
            textSize = 13f
            setPadding(28, 28, 28, 28)
            gravity = Gravity.START
            setTextIsSelectable(true)
            text = "A navegacao nao conseguiu abrir.\n${carimbo()}\n\n$texto"
        }
        setContentView(ScrollView(this).apply { addView(corpo) })
    }

    /**
     * O manifesto prende em paisagem para o app abrir sem piscar; daqui em
     * diante vale a escolha guardada. "auto" segue o sensor, com as quatro
     * posicoes — tablet de cabine as vezes fica de cabeca para baixo no
     * suporte, e o sensor resolve sozinho.
     */
    private fun aplicaOrientacao(modo: String) {
        requestedOrientation = when (modo) {
            "retrato" -> ActivityInfo.SCREEN_ORIENTATION_SENSOR_PORTRAIT
            "auto" -> ActivityInfo.SCREEN_ORIENTATION_FULL_SENSOR
            else -> ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun monta() {
        config = Config(this)
        aplicaOrientacao(config.orientacao)
        posicao = Posicao(this).apply { limiteParadoKmh = config.limiteParadoKmh }
        sinc = Sincronizacao(this, config)
        sinc.semeiaSePreciso(this)
        voz = Voz(this)
        api = Api(posicao, sinc, config).apply {
            ultimaFalha = falhaAnterior
            versaoApp = carimbo()
            voz = this@MainActivity.voz
        }

        // tela sempre acesa: o operador nao vai destravar o tablet dirigindo
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        val carregador = WebViewAssetLoader.Builder()
            .setDomain("appassets.androidplatform.net")
            .addPathHandler("/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()

        web = WebView(this)
        setContentView(web)
        // so' agora: antes do setContentView nao existe DecorView, e pedir o
        // controlador de insets a uma janela sem decor devolve null
        telaCheia()
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true          // a interface guarda tema e camadas
            cacheMode = WebSettings.LOAD_NO_CACHE
            setSupportZoom(false)
            builtInZoomControls = false
            mediaPlaybackRequiresUserGesture = false
        }
        web.setBackgroundColor(0xFF000000.toInt())
        web.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(
                v: WebView, pedido: WebResourceRequest
            ): WebResourceResponse? {
                val u = pedido.url
                if (u.host != "appassets.androidplatform.net") return null

                if (u.path == "/orientacao") {
                    val modo = u.getQueryParameter("modo") ?: ""
                    if (modo !in MODOS) {
                        return resposta(Json.erro("modo deve ser paisagem, retrato ou auto"))
                    }
                    config.orientacao = modo
                    // este metodo roda fora da thread da tela, e girar e'
                    // coisa que so' a thread da tela pode pedir
                    runOnUiThread { aplicaOrientacao(modo) }
                    return resposta("""{"ok":true,"orientacao":"$modo"}""")
                }
                if (u.path == "/config") {
                    // o WebView nao entrega o corpo do POST aqui; por isso a
                    // pagina manda o endereco tambem na URL
                    val novo = u.getQueryParameter("servidor") ?: ""
                    return resposta(api.mudaConfig(
                        """{"servidor":"${Json.escapa(novo)}"}"""))
                }
                // consulta crua: Api.parametros decodifica. Com u.query ela era
                // decodificada duas vezes e "+" ou "%" num nome estragavam o texto
                api.responde(u.path ?: "", u.encodedQuery)?.let { return it }
                return carregador.shouldInterceptRequest(u)
            }
        }
        // A URL da tela e' sempre a mesma entre versoes, entao o WebView pode
        // servir a copia guardada dele e mostrar a interface antiga depois de
        // atualizar o app. Limpar o cache e pendurar a data de instalacao na
        // URL forcam a leitura do asset novo.
        web.clearCache(true)
        val instalado = try {
            packageManager.getPackageInfo(packageName, 0).lastUpdateTime
        } catch (e: Exception) {
            0L
        }
        web.loadUrl("$BASE/interface.html?v=$instalado")
        montou = true

        pedeLocalizacao()
        // de minuto em minuto: quem decide se ha conversa de verdade e' a
        // propria sincronizacao. Assim o app reencontra o servidor logo depois
        // de uma queda, em vez de esperar cinco minutos.
        agenda.scheduleWithFixedDelay({
            try { sinc.sincroniza() } catch (_: Exception) { }
        }, 0, 60, TimeUnit.SECONDS)
    }

    private fun resposta(corpo: String) = WebResourceResponse(
        "application/json", "utf-8",
        ByteArrayInputStream(corpo.toByteArray(Charsets.UTF_8))
    )

    private fun pedeLocalizacao() {
        val concedida = checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) ==
                PackageManager.PERMISSION_GRANTED
        if (concedida) {
            posicao.iniciar()
            return
        }
        // negada de vez, o sistema nao pergunta mais: dizer isso na tela e' a
        // diferenca entre resolver em dez segundos e procurar defeito no codigo
        if (!shouldShowRequestPermissionRationale(Manifest.permission.ACCESS_FINE_LOCATION)
            && jaPediuAntes()) {
            posicao.semPermissao()
            return
        }
        marcaQuePediu()
        requestPermissions(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION),
                           PEDIDO_LOCALIZACAO)
    }

    private fun jaPediuAntes() =
        getSharedPreferences("navegacao", MODE_PRIVATE).getBoolean("pediu_gps", false)

    private fun marcaQuePediu() =
        getSharedPreferences("navegacao", MODE_PRIVATE)
            .edit().putBoolean("pediu_gps", true).apply()

    override fun onRequestPermissionsResult(
        codigo: Int, permissoes: Array<out String>, resultados: IntArray
    ) {
        super.onRequestPermissionsResult(codigo, permissoes, resultados)
        if (codigo == PEDIDO_LOCALIZACAO &&
            resultados.isNotEmpty() && resultados[0] == PackageManager.PERMISSION_GRANTED) {
            posicao.iniciar()
        } else if (codigo == PEDIDO_LOCALIZACAO) {
            posicao.semPermissao()
        }
    }

    /**
     * Esconde as barras do sistema. So' funciona depois do setContentView: e'
     * o DecorView que carrega o controlador de insets.
     *
     * Envolvido em try/catch de proposito. Isto e' acabamento; derrubar a
     * navegacao por causa de barra de status seria trocar o que importa pelo
     * que enfeita.
     */
    private fun telaCheia() {
        try {
            val decor = window.peekDecorView() ?: return
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                window.setDecorFitsSystemWindows(false)
                decor.windowInsetsController
                    ?.hide(android.view.WindowInsets.Type.systemBars())
            } else {
                @Suppress("DEPRECATION")
                decor.systemUiVisibility =
                    View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or
                    View.SYSTEM_UI_FLAG_FULLSCREEN or
                    View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
                    View.SYSTEM_UI_FLAG_LAYOUT_STABLE
            }
        } catch (e: Throwable) {
            android.util.Log.w("MainActivity", "nao consegui esconder as barras", e)
        }
    }

    override fun onWindowFocusChanged(temFoco: Boolean) {
        super.onWindowFocusChanged(temFoco)
        if (temFoco && montou) telaCheia()
    }

    override fun onDestroy() {
        // se a montagem falhou, estes campos nao existem: tocar neles aqui
        // trocaria a tela de erro por outro fechamento
        if (montou) posicao.parar()
        voz?.encerra()
        agenda.shutdownNow()
        super.onDestroy()
    }

    /** Voltar nao sai do app: e' aplicacao de operacao, nao navegador. */
    @Deprecated("comportamento intencional")
    override fun onBackPressed() {
        if (!montou) { super.onBackPressed(); return }
        web.evaluateJavascript(
            "(function(){var m=document.getElementById('menu');" +
            "var f=document.getElementById('painelFogo');" +
            "if(f&&f.style.display==='block'){f.style.display='none';return 1;}" +
            "if(m&&m.style.display==='block'){m.style.display='none';return 1;}" +
            "return 0;})()", null)
    }
}
