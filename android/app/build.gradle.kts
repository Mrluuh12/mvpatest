plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "br.com.mina.navegacao"
    compileSdk = 34

    defaultConfig {
        applicationId = "br.com.mina.navegacao"
        // Android 7: tablet de campo costuma ser antigo
        minSdk = 24
        targetSdk = 34
        versionCode = 5
        versionName = "1.4"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"),
                          "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

// A interface e' a MESMA do PTX, e as duas copias sao mantidas iguais pelo
// teste `test_a_interface_do_android_e_a_mesma_do_ptx` — nao por uma tarefa de
// build.
//
// Aqui havia um Copy que trazia ../ptx/interface.html para os assets a cada
// build. Parecia garantir a sincronia; na pratica fazia o contrario. Quem
// recebe so' a pasta android/ e a extrai ao lado de um ptx/ antigo tem a tela
// nova do APK sobrescrita pela velha, calada, toda vez que compila — e o
// aparelho mostra uma interface que ninguem mais tem no repositorio. Foi o que
// aconteceu em campo. O mesmo valia para a semente do mapa.
//
// O asset versionado e' a fonte. Nada no build o reescreve.

dependencies {
    // Uma dependencia so'. O webkit traz o WebViewAssetLoader, que serve os
    // arquivos locais sem file:// (bloqueado para XHR nas versoes novas).
    //
    // Nada de appcompat: a tela e' um WebView, e AppCompatActivity exige um
    // tema Theme.AppCompat — se ele nao resolve, a falha acontece dentro do
    // super.onCreate, antes de qualquer try/catch nosso, e o app fecha sem
    // dizer nada. Activity da plataforma com tema da plataforma nao tem esse
    // acoplamento.
    implementation("androidx.webkit:webkit:1.11.0")
    testImplementation("junit:junit:4.13.2")
}
