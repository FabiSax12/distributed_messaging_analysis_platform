# Current architecture

```mermaid
flowchart LR
    UI[React dashboard - Camila integration] --> CF[CloudFront]
    Load[Manifest load client] --> CF
    Cog[Cognito access tokens] --> UI
    CF --> TLS[Optional ACM HTTPS ALB - DNS setup required]
    TLS --> API[Coordinator TLS target]
    CF -. Existing HTTP origin until TLS activated .-> API
    API --> DB[PostgreSQL state and hash-chained custody]
    API --> Q[RabbitMQ: bounded publish window / mutable length policy]
    Q --> T[Text process pool]
    Q --> I[YOLO CPU process pool]
    Q --> A[Whisper CPU process pool]
    T --> S3[S3 original evidence and SHA-256 CAS]
    I --> S3
    A --> S3
    Q --> API
    API --> CW[CloudWatch metrics / alarms]
    API --> SNS[SNS severe / operational alerts]
    CW --> SNS
```

Analysis runs in killable child processes under per-node Docker cgroup limits.
RDS uses TLS; MQ uses AMQPS; S3 uses HTTPS and SSE-KMS. TLS-origin activation
uses a publicly validated certificate at the ALB and encryption to a self-signed
coordinator target; ALB does not authenticate that target certificate. Security
groups restrict the target hop and the origin header is checked at the app.
Cloud activation and measurements remain separate from implementation status.
