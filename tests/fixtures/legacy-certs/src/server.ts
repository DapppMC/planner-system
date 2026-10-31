import express from "express";
import { handleSliceCert, listCerts, removeCert } from "./api/certs";
import { requireAuth } from "./api/auth";

const app = express();
app.use(express.json());

app.get("/certs", requireAuth, listCerts);
app.post("/certs/slice", requireAuth, handleSliceCert);
app.delete("/certs/:id", requireAuth, removeCert);

app.listen(process.env.PORT || 3000);
