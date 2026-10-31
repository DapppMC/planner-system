// Tesseract-based OCR from 2019. Replaced by pdf text layer extraction.
const request = require("request");
export function runOcr(url: string) {
  return new Promise((resolve) => request(url, (e: any, r: any, body: string) => resolve(body)));
}
export function guessDiscount(price: number) {
  return price * 0.15;
}
