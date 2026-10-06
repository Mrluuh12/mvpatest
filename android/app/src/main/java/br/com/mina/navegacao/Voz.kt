package br.com.mina.navegacao

import android.content.Context
import android.speech.tts.TextToSpeech
import android.util.Log
import java.util.Locale

/**
 * Instrucao falada: "Em 200 metros, vire a direita".
 *
 * Usa o sintetizador do proprio Android, sem rede: na cava o Wi-Fi cai, e a
 * voz e' justamente para quando o operador nao pode olhar a tela.
 *
 * Tablet sem voz em portugues instalada nao fala — e diz isso em [estado],
 * que aparece no menu. Falar em ingles com sotaque de leitura de numero seria
 * pior que silencio.
 */
class Voz(ctx: Context) : TextToSpeech.OnInitListener {

    companion object {
        private const val TAG = "Voz"
    }

    private val tts = TextToSpeech(ctx.applicationContext, this)

    @Volatile var estado = "iniciando"; private set
    @Volatile private var pronta = false

    override fun onInit(status: Int) {
        if (status != TextToSpeech.SUCCESS) {
            estado = "sem sintetizador de voz no tablet"
            return
        }
        val r = tts.setLanguage(Locale.forLanguageTag("pt-BR"))
        if (r == TextToSpeech.LANG_MISSING_DATA || r == TextToSpeech.LANG_NOT_SUPPORTED) {
            estado = "sem voz em portugues — Ajustes > Idioma > Saida de texto em voz"
            return
        }
        pronta = true
        estado = "pronta"
    }

    /**
     * Urgente ("vire a direita" na hora, area de desmonte) corta o que estiver
     * sendo dito: instrucao velha falada depois do cruzamento so' atrapalha.
     * O resto entra na fila, para "rota tracada" nao ser engolida pela
     * primeira instrucao que vem logo atras.
     */
    fun fala(texto: String, urgente: Boolean = false) {
        if (!pronta || texto.isBlank()) return
        val modo = if (urgente) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD
        try {
            tts.speak(texto.take(300), modo, null, "navegacao")
        } catch (e: Exception) {
            Log.w(TAG, "nao consegui falar", e)
        }
    }

    fun encerra() {
        try {
            tts.stop()
            tts.shutdown()
        } catch (_: Exception) {
        }
    }
}
