// Configuración de la extensión. Sin código remoto: todo lo que corre está aquí.
//
// `apiBase` es a dónde se envía la sesión. Si cambias el valor por defecto,
// cambia también `host_permissions` en manifest.json con el mismo origen; si
// no, el navegador bloquea el envío. Para probar contra otro servidor sin tocar
// archivos, usa «Servidor de Data Effi» en la propia ventanita: pide permiso
// solo para ese origen.
export const CONFIG = Object.freeze({
  apiBase: "https://master-data-api.onrender.com",
  redeemPath: "/config/effi/pairing/redeem",

  // Dónde vive Effi. Las cookies se buscan en este dominio y sus subdominios.
  effiDomain: "effi.com.co",
  effiLoginUrl: "https://effi.com.co/ingreso",

  // Las ÚNICAS cookies que se envían. Effi es CodeIgniter: la sesión es
  // `ci_session`. Todo lo demás (analítica, preferencias) no sale del navegador.
  // La primera es obligatoria. Si Effi resulta necesitar otra, se agrega aquí y
  // en EFFI_EXTRA_SESSION_COOKIES del servidor.
  sessionCookies: ["ci_session"],
});
