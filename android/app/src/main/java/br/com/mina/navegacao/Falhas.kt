package br.com.mina.navegacao

import android.content.Context
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter

/**
 * Guarda o motivo de um fechamento inesperado.
 *
 * No tablet nao ha logcat aberto: o app "abre e fecha" e ninguem fica sabendo
 * por que. Aqui a pilha e' gravada em arquivo e mostrada na proxima abertura,
 * para o erro chegar em quem esta' com o aparelho na mao.
 */
object Falhas {

    private const val ARQUIVO = "ultima_falha.txt"

    fun instalar(ctx: Context) {
        val anterior = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { linha, erro ->
            try {
                grava(ctx, erro)
            } catch (_: Throwable) {
            }
            anterior?.uncaughtException(linha, erro)
        }
    }

    fun grava(ctx: Context, erro: Throwable) {
        val texto = StringWriter()
        PrintWriter(texto).use { erro.printStackTrace(it) }
        File(ctx.filesDir, ARQUIVO).writeText(
            java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US)
                .format(java.util.Date()) + "\n" + texto.toString())
    }

    fun ultima(ctx: Context): String? {
        val f = File(ctx.filesDir, ARQUIVO)
        return if (f.exists()) f.readText() else null
    }

    fun limpa(ctx: Context) {
        try { File(ctx.filesDir, ARQUIVO).delete() } catch (_: Exception) { }
    }

    fun comoTexto(erro: Throwable): String {
        val texto = StringWriter()
        PrintWriter(texto).use { erro.printStackTrace(it) }
        return texto.toString()
    }
}
