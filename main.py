import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_secretsmanager as secretsmanager,
    aws_iam as iam,
    aws_logs as logs,
    aws_elasticloadbalancingv2 as elbv2,
)
from constructs import Construct

class SonarQubeStack(cdk.Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # VPC
        vpc = ec2.Vpc(
            self, "SonarQubeVPC",
            max_azs=2,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC
                ),
                ec2.SubnetConfiguration(
                    name="private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                )
            ]
        )

        # Database (RDS PostgreSQL)
        db_secret = secretsmanager.Secret(
            self, "SonarQubeDBSecret",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username": "sonar"}',
                generate_string_key="password"
            )
        )

        db_subnet_group = rds.SubnetGroup(
            self, "SonarQubeDBSubnetGroup",
            vpc=vpc,
            subnet_group_name="sonarqube-db-subnet-group",
            description="Subnet group for SonarQube database"
        )

        db_security_group = ec2.SecurityGroup(
          self, "SonarQubeDBSecurityGroup",
          vpc=vpc,
          description="Allow SonarQube ECS to access RDS",
          allow_all_outbound=False
        )

        db_security_group.add_ingress_rule(
            ec2.Peer.ipv4(vpc.vpc_cidr_block),
            ec2.Port.tcp(5432),
            "Allow SonarQube ECS to access RDS"
        )

        database = rds.DatabaseInstance(
            self, "SonarQubeDatabase",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_14
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE3, ec2.InstanceSize.MEDIUM
            ),
            vpc=vpc,
            credentials=rds.Credentials.from_secret(db_secret),
            vpc_subnets=rds.SubnetSelection(subnet_group=db_subnet_group),
            security_groups=[db_security_group],
            database_name="sonarqube",
            allocated_storage=20,
            storage_type=rds.StorageType.GP2,
            multi_az=True,
            removal_policy=cdk.RemovalPolicy.DESTROY, # Adjust for production
            deletion_protection=False, # Adjust for production
        )

        # ECS Cluster
        cluster = ecs.Cluster(
            self, "SonarQubeCluster",
            vpc=vpc
        )

        # SonarQube Task Definition
        task_definition = ecs.FargateTaskDefinition(
            self, "SonarQubeTaskDefinition",
            cpu=2048,
            memory_limit_mib=4096,
        )

        log_group = logs.LogGroup(
            self, "SonarQubeLogGroup",
            log_group_name="/ecs/sonarqube",
            removal_policy=cdk.RemovalPolicy.DESTROY
        )

        container = task_definition.add_container(
            "SonarQubeContainer",
            image=ecs.ContainerImage.from_docker_hub("sonarqube:enterprise"), # Use enterprise image
            memory_limit_mib=4096,
            cpu=2048,
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="sonarqube", log_group=log_group
            ),
            environment={
                "SONAR_JDBC_URL": f"jdbc:postgresql://{database.db_instance_endpoint_address}:{database.db_instance_endpoint_port}/sonarqube",
                "SONAR_JDBC_USERNAME": "sonar",
                "SONAR_JDBC_PASSWORD": db_secret.secret_value.unsafe_unwrap(),
                "SONAR_WEB_JAVAOPTS": "-Xmx2048m -Xms256m -XX:+HeapDumpOnOutOfMemoryError",
                "SONAR_SEARCH_JAVAOPTS": "-Xmx2048m -Xms256m -XX:+HeapDumpOnOutOfMemoryError",

            },
            port_mappings=[ecs.PortMapping(container_port=9000)]
        )

        # ECS Service
        service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self, "SonarQubeService",
            cluster=cluster,
            task_definition=task_definition,
            public_load_balancer=True,
            desired_count=1,
            security_groups=[ec2.SecurityGroup(self, "SonarQubeServiceSG", vpc=vpc, allow_all_outbound=True)]
        )

        service.target_group.configure_health_check(path="/sessions/new")

        # Allow ECS access to the DB secret
        db_secret.grant_read(service.task_definition.task_role)

app = cdk.App()
SonarQubeStack(app, "SonarQubeStack", env=cdk.Environment(account="YOUR_ACCOUNT_ID", region="YOUR_REGION")) #Replace with your account and region.
app.synth()
