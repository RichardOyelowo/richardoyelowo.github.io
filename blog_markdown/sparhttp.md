---
title: "Why I Built SparHTTP and What Writing a Server in C Taught Me"
date: "2024-09-25"
summary: "High-level frameworks hide the network. I wanted to see exactly what happens when a request hits a server, so I built one from scratch in C."
pinned: true
---

# Why I Built SparHTTP and What Writing a Server in C Taught Me

## The Problem I Kept Solving

I write Python for my production APIs, but my foundation is in C from Harvard's CS50. When you use FastAPI or Flask, the framework hides the network completely. You never see the socket. You never read the raw bytes. You never write the HTTP formatting yourself. I kept wondering what actually happens at the boundary when a client connects and sends a request. I wanted to strip away the abstractions and build an HTTP server from scratch. That project became SparHTTP.

## What SparHTTP Actually Does

SparHTTP is a lightweight HTTP server written in pure C. It opens a raw TCP socket, binds it to a local port, and listens for a connection. When a client connects, it reads the request off the wire, parses the request line and the headers, builds a fixed response, and sends it back. Right now it handles one client per run and exits after that. This is the first server in a series I'm planning to build across a few languages, so the goal here was to get the raw mechanics right in C before moving on to anything else.

## Design Decisions

SparHTTP uses zero dependencies, just the C standard library and POSIX networking headers. There's no malloc anywhere. Every buffer, the read buffer, the header array, the request line struct, is a fixed size and lives on the stack.

It handles:

- Socket creation, binding, and listening
- Reading the raw request off the socket
- Parsing the request line into method, path, and version
- Parsing headers into a name and value array
- Building and sending back a response

It does not handle yet:

- More than one client per run
- Routing by method or path (every request gets the same 200 OK)
- Malformed or missing input
- Thread pools or an event loop

## The Code I Wanted to Write

Here's how the server gets created and how it accepts a client, from `server.c`:

```c
int create_server() {
    int server_socket = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in server_address;
    socklen_t address_length = sizeof(server_address);

    server_address.sin_family = AF_INET;
    server_address.sin_port = htons(PORT);
    inet_pton(AF_INET, ADDRESS, &server_address.sin_addr);

    int reuse = 1;
    setsockopt(server_socket, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));

    if (bind(server_socket, (struct sockaddr *)&server_address, address_length) < 0)
        printf("Binding Error Occurred\n");

    if (listen(server_socket, BACKLOG) < 0)
        printf("Listen Error Occurred\n");

    return server_socket;
}

int accept_client(int server_socket) {
    struct sockaddr_in client_address;
    socklen_t address_length = sizeof(client_address);
    return accept(server_socket, (struct sockaddr *)&client_address, &address_length);
}
```

And here's the actual parsing from `http.c`. It walks the buffer with a `current` pointer and cuts each line at `\r`:

```c
while (current[0] != '\r' || current[1] != '\n') {
    char *end = strchr(current, '\r');
    if (end == NULL)
        break;
    *end = '\0';

    if (line_count == 0) {
        char *first_space = strchr(current, ' ');
        size_t method_len = first_space - current;
        memcpy(request_line->method, current, method_len);
        request_line->method[method_len] = '\0';
        current = first_space + 1;
        // path and version get pulled out the same way
    }
}
```

The first line parsed is the request line, method, path, version. Every line after that gets read as a `Name: value` header, until the code hits the blank line marking the end of the headers.

## The Bug I Haven't Fixed Yet

This code doesn't validate malformed input, and I found the gap while testing it by hand. If a client sends a request line with no spaces in it, `strchr(current, ' ')` returns NULL. The next line, `method_len = first_space - current`, does pointer arithmetic on that null pointer. That's undefined behavior, and in practice it crashes the process. In Python you'd get a clean exception and a traceback. In C you get a dead process and have to go dig for the cause yourself.

I know where the fix goes, a null check right after each `strchr` call, before touching the result. I just haven't written it yet. It's next on the list.

## What I Would Do Differently

Handle more than one client. Right now the server accepts a single connection and exits. A real server needs a loop, or better, an event-driven model using epoll on Linux or kqueue on Mac, so one process can manage many connections without blocking on each one.

Add real routing. Every request gets the same 200 OK response right now, regardless of method or path. A GET to `/health` and a POST to `/users` get identical treatment. Routing by method and path is the next piece.

Validate the input. As above, the parser currently trusts whatever the client sends. That's the fastest way to turn one malformed request into a crash.

## The Takeaway

SparHTTP won't replace Nginx or handle production traffic, and that was never the plan. Writing it taught me how sockets work, how HTTP text is structured on the wire, and how much a language like C leaves for you to check yourself. If you want to see what your framework hides, building a server from scratch is the fastest way to find out.

Code's here if you want to look through it or build it yourself: github.com/RichardOyelowo/SparHTTP
