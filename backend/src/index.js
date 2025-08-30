const express = require("express");
const app = express();
const http = require("http").createServer(app);
const io = require("socket.io")(http);
const port = 3000;

app.use(express.json());

app.post('/alert', (req, res) => {
 const {crypto, current_vol, avg_vol, threshold} = req.body;
  io.emit('trade_update', {crypto, current_vol, avg_vol, threshold});  
  res.sendStatus(200);
});

app.get("/", (req, res) => {
  res.send("Backend running");
});

http.listen(port, () => {
  console.log(`Backend listening at http://localhost:${port}`);
});
