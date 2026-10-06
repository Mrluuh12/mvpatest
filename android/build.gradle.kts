// O toolchain do Android (AGP 8.5 / Kotlin 1.9) vai ate' o JDK 21. Rodando
// num JDK mais novo, o Kotlin quebra ao interpretar a versao e o erro que
// aparece e' "IllegalArgumentException: 25.0.2" no meio de um stack trace do
// daemon, sem dizer o que fazer. Melhor falhar aqui, dizendo.
val versaoJava = (System.getProperty("java.version") ?: "")
val maiorJava = versaoJava.substringBefore(".").toIntOrNull() ?: 0
if (maiorJava > 21) {
    throw GradleException(
        """
        JDK $versaoJava nao serve para este projeto. Use o 21.

        Nenhum toolchain do Android compila com JDK 25 ou mais novo; o
        Android Studio recente ja' vem com um JBR novo demais, entao pode ser
        preciso baixar o 21 em vez de usar o embutido.

        No Android Studio:
          Settings > Build, Execution, Deployment > Build Tools > Gradle
          Gradle JDK > Download JDK...
            Version: 21          <- e' esta a linha que importa
            Vendor : Eclipse Temurin (ou Azul Zulu)
          e depois selecione esse 21 no Gradle JDK.

        Por fim:  Build > Clean Project
        A pasta app/build guarda cache do Kotlin e precisa ir junto.
        """.trimIndent()
    )
}

plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
}
