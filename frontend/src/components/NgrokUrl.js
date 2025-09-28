import React, { useState, useEffect } from "react";
import "./NgrokUrl.css";

function NgrokUrl() {
  const [ngrokUrl, setNgrokUrl] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const fetchNgrokUrl = async () => {
      try {
        const response = await fetch("http://localhost:4040/api/tunnels");
        if (!response.ok) {
          throw new Error("ngrok not available");
        }
        const data = await response.json();

        if (data.tunnels && data.tunnels.length > 0) {
          const httpsTunnel = data.tunnels.find(t => t.proto === "https") || data.tunnels[0];
          setNgrokUrl(httpsTunnel.public_url);
          setError(null);
        } else {
          setError("no active tunnels");
        }
      } catch (err) {
        setError("ngrok offline");
      } finally {
        setLoading(false);
      }
    };

    fetchNgrokUrl();
    const interval = setInterval(fetchNgrokUrl, 10000);

    return () => clearInterval(interval);
  }, []);

  const handleCopy = () => {
    if (ngrokUrl) {
      navigator.clipboard.writeText(ngrokUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  if (loading) {
    return (
      <div className="ngrok-container loading">
        <span className="ngrok-label">mobile access:</span>
        <span className="ngrok-status">checking...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="ngrok-container error">
        <span className="ngrok-label">mobile access:</span>
        <span className="ngrok-status">{error}</span>
      </div>
    );
  }

  return (
    <div className="ngrok-container active">
      <span className="ngrok-label">mobile access:</span>
      <a
        href={ngrokUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="ngrok-url"
      >
        {ngrokUrl}
      </a>
      <button
        onClick={handleCopy}
        className="copy-button"
        title="Copy URL"
      >
        {copied ? "✓" : "📋"}
      </button>
    </div>
  );
}

export default NgrokUrl;