/* Sample VmHWM until a child exits.

Linux ru_maxrss in this environment follows the cgroup high-water mark, so
every process looks the same size. VmHWM from /proc/<pid>/status is the
process peak, and it disappears once the process is reaped, so this loop
reads it while the child is still alive.

Usage: rss-sample <peak-file> -- command [args...]
The peak file receives "<rss_kb> <elapsed_ms>". The exit status is the child's.
*/
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static int vmhwm_kb(pid_t pid) {
    char path[64];
    char buf[4096];
    int fd;
    ssize_t n;
    char *line;

    snprintf(path, sizeof path, "/proc/%d/status", (int)pid);
    fd = open(path, O_RDONLY);
    if (fd < 0) {
        return -1;
    }
    n = read(fd, buf, sizeof buf - 1);
    close(fd);
    if (n <= 0) {
        return -1;
    }
    buf[n] = '\0';
    line = strstr(buf, "VmHWM:");
    if (line == NULL) {
        return -1;
    }
    line += 6;
    while (*line != '\0' && isspace((unsigned char)*line)) {
        line++;
    }
    return atoi(line);
}

int main(int argc, char **argv) {
    struct timespec start;
    struct timespec end;
    pid_t pid;
    int status = 0;
    int max_kb = 0;
    double elapsed_ms;
    FILE *out;

    if (argc < 4 || strcmp(argv[2], "--") != 0) {
        fprintf(stderr, "usage: rss-sample <peak-file> -- command [args...]\n");
        return 2;
    }
    clock_gettime(CLOCK_MONOTONIC, &start);
    pid = fork();
    if (pid < 0) {
        return 127;
    }
    if (pid == 0) {
        execvp(argv[3], argv + 3);
        _exit(127);
    }
    for (;;) {
        int kb = vmhwm_kb(pid);
        pid_t got;
        if (kb > max_kb) {
            max_kb = kb;
        }
        got = waitpid(pid, &status, WNOHANG);
        if (got == pid) {
            break;
        }
        if (got < 0 && errno != EINTR) {
            break;
        }
    }
    clock_gettime(CLOCK_MONOTONIC, &end);
    elapsed_ms = (double)(end.tv_sec - start.tv_sec) * 1000.0 +
        (double)(end.tv_nsec - start.tv_nsec) / 1000000.0;
    out = fopen(argv[1], "w");
    if (out == NULL) {
        return 127;
    }
    fprintf(out, "%d %.3f\n", max_kb, elapsed_ms);
    fclose(out);
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    if (WIFSIGNALED(status)) {
        return 128 + WTERMSIG(status);
    }
    return 1;
}
