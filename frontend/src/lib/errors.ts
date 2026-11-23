import { ApiError } from "./api";

const TRANSLATIONS: [RegExp, string][] = [
  [/no active account/i, "Correo o contraseña incorrectos."],
  [/too short/i, "Usa al menos 8 caracteres."],
  [/too common/i, "Esa contraseña es muy común. Elige otra."],
  [/entirely numeric/i, "La contraseña no puede tener solo números."],
  [/too similar/i, "La contraseña se parece demasiado a tu correo."],
  [/valid email/i, "Escribe un correo válido."],
  [/may not be blank|is required/i, "Este campo es obligatorio."],
  [/no more than/i, "El texto es demasiado largo."],
  [/invalid or has expired/i, "Este enlace no es válido o ya caducó."],
  [/verify your email/i, "Confirma tu correo para poder subir videos."],
  [/exceeds the/i, "El archivo supera el tamaño máximo."],
  [/could not be sent/i, "No se pudo enviar el correo. Inténtalo de nuevo."],
  [/storage is temporarily/i, "El almacenamiento no responde. Inténtalo de nuevo."],
  [/processing could not be started/i, "No se pudo iniciar el procesamiento. Inténtalo de nuevo."],
  [/could not be deleted/i, "No se pudo borrar el video. Inténtalo de nuevo."],
  [/not been uploaded yet/i, "Faltan partes del archivo por subir."],
  [/failed or was abandoned/i, "Esta subida falló o fue abandonada."],
  [/abandoned before/i, "La subida se abandonó antes de terminar."],
  [/not a supported video/i, "El archivo no es un video compatible."],
  [/no video stream/i, "El archivo no tiene pista de video."],
  [/could not be analyzed/i, "No se pudo analizar el archivo."],
  [/resolution is not supported/i, "La resolución del video no es compatible."],
  [/duration could not/i, "No se pudo determinar la duración del video."],
  [/longer than the allowed/i, "El video supera la duración máxima."],
  [/took too long/i, "El procesamiento tardó demasiado y se detuvo."],
  [/ffmpeg could not/i, "FFmpeg no pudo procesar este archivo."],
  [/temporary disk space/i, "No hay espacio en disco para procesar este video."],
  [/storage was unavailable/i, "El almacenamiento no respondió durante el proceso."],
  [/kept crashing/i, "El procesamiento falló varias veces y se detuvo."],
  [/processing failed/i, "El procesamiento falló."],
];

const GENERIC = "Algo salió mal. Inténtalo de nuevo.";

export function translate(message: string) {
  return TRANSLATIONS.find(([pattern]) => pattern.test(message))?.[1] ?? GENERIC;
}

const first = (value: unknown) => (Array.isArray(value) ? value[0] : value);

/** Validation errors by field, ready to show next to each input. */
export function fieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError) || error.status !== 400) return {};
  if (typeof error.data !== "object" || error.data === null) return {};
  return Object.fromEntries(
    Object.entries(error.data)
      .filter(([, value]) => typeof first(value) === "string")
      .map(([field, value]) => [field, translate(first(value) as string)]),
  );
}

export function errorMessage(error: unknown): string {
  if (error instanceof TypeError) return "No hay conexión con el servidor.";
  if (!(error instanceof ApiError)) return GENERIC;
  if (error.status === 429) return "Demasiados intentos. Espera un momento y vuelve a probar.";
  if (error.status >= 500 && error.status !== 503) return GENERIC;
  const data = error.data as { detail?: unknown } | null;
  if (typeof data?.detail === "string") return translate(data.detail);
  const [message] = Object.values(fieldErrors(error));
  return message ?? GENERIC;
}
