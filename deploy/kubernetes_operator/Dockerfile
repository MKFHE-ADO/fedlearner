FROM golang:1.13

WORKDIR /go/src/github.com/bytedance/fedlearner/deploy/kubernetes_operator

COPY go.mod .
COPY go.sum .

RUN git clone https://github.com/coreos/go-systemd.git /go/src/github.com/coreos/go-systemd

ENV GO111MODULE=on \
    GOPROXY="https://goproxy.cn,https://proxy.golang.org,direct" \
    GOSUMDB="sum.golang.google.cn"
RUN go mod download

COPY . .
RUN go build main.go
CMD ["sh"]

