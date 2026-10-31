import express from "express";
const app = express();
const router = express.Router();
app.get("/certs", listCerts);
app.post("/certs/slice", auth, handleSliceCert);
router.delete("/certs/:id", removeCert);
app.use(express.json());
app.listen(3000);
const x = obj.get("not-a-route-but-matches-get-shape-on-other-object");
